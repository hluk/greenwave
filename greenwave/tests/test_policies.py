
# SPDX-License-Identifier: GPL-2.0+

import pytest
import mock
import time

from textwrap import dedent

from greenwave.app_factory import create_app
from greenwave.policies import (
    load_policies,
    summarize_answers,
    Policy,
    RemotePolicy,
    RemoteRule,
    RuleSatisfied,
    TestResultMissing,
    TestResultFailed,
    TestResultPassed,
    InvalidRemoteRuleYaml,
    MissingRemoteRuleYaml,
    OnDemandPolicy
)
from greenwave.resources import ResultsRetriever
from greenwave.safe_yaml import SafeYAMLError
from greenwave.subjects.factory import create_subject
from greenwave.waivers import waive_answers
from greenwave.config import TestingConfig, Config


def create_test_subject(type_id, item):
    app = create_app('greenwave.config.TestingConfig')
    with app.app_context():
        return create_subject(type_id, item)


class DummyResultsRetriever(ResultsRetriever):
    def __init__(self, subject=None, testcase=None, outcome='PASSED'):
        super(DummyResultsRetriever, self).__init__(
            ignore_ids=[],
            when='',
            url='')
        self.subject = subject
        self.testcase = testcase
        self.outcome = outcome
        self.external_cache = {}
        self.retrieve_data_called = 0

    def _retrieve_data(self, params):
        self.retrieve_data_called += 1
        if (self.subject and (params.get('item') == self.subject.identifier or
                              params.get('nvr') == self.subject.identifier) and
                ('type' not in params or self.subject.type in params['type'].split(',')) and
                (params.get('testcases') is None or params.get('testcases') == self.testcase)):
            return [{
                'id': 123,
                'data': {
                    'item': [self.subject.identifier],
                    'type': [self.subject.type],
                },
                'testcase': {'name': self.testcase},
                'outcome': self.outcome,
            }]
        return []

    def get_external_cache(self, key):
        return self.external_cache.get(key)

    def set_external_cache(self, key, value):
        self.external_cache[key] = value


def test_summarize_answers():
    subject = create_test_subject('koji_build', 'nvr')
    assert summarize_answers([RuleSatisfied()]) == \
        'All required tests passed'
    assert summarize_answers([TestResultFailed(subject, 'test', None, 'id'),
                              RuleSatisfied()]) == \
        '1 of 2 required tests failed'
    assert summarize_answers([TestResultMissing(subject, 'test', None)]) == \
        '1 of 1 required test results missing'
    assert summarize_answers([TestResultMissing(subject, 'test', None),
                              TestResultFailed(subject, 'test', None, 'id')]) == \
        '1 of 2 required tests failed, 1 result missing'
    assert summarize_answers([TestResultMissing(subject, 'testa', None),
                              TestResultMissing(subject, 'testb', None),
                              TestResultFailed(subject, 'test', None, 'id')]) == \
        '1 of 3 required tests failed, 2 results missing'
    assert summarize_answers([TestResultMissing(subject, 'test', None),
                             RuleSatisfied()]) == \
        '1 of 2 required test results missing'


def test_decision_with_missing_result(tmpdir):
    p = tmpdir.join('fedora.yaml')
    p.write(dedent("""
        --- !Policy
        id: "rawhide_compose_sync_to_mirrors"
        product_versions:
          - fedora-rawhide
        decision_context: rawhide_compose_sync_to_mirrors
        subject_type: compose
        rules:
          - !PassingTestCaseRule {test_case_name: sometest}
        """))
    policies = load_policies(tmpdir.strpath)
    policy = policies[0]

    results = DummyResultsRetriever()
    subject = create_test_subject('koji_build', 'some_nevr')

    # Ensure that absence of a result is failure.
    decision = policy.check('fedora-rawhide', subject, results)
    assert len(decision) == 1
    assert isinstance(decision[0], TestResultMissing)


def test_waive_brew_koji_mismatch(tmpdir):
    """ Ensure that a koji_build waiver can match a brew-build result

    Note that 'brew-build' in the result does not match 'koji_build' in the
    waiver.  Even though these are different strings, this should work.
    """

    p = tmpdir.join('fedora.yaml')
    p.write(dedent("""
        --- !Policy
        id: some_id
        product_versions:
        - fedora-rawhide
        decision_context: test
        subject_type: koji_build
        rules:
          - !PassingTestCaseRule {test_case_name: sometest}
        """))
    policies = load_policies(tmpdir.strpath)
    policy = policies[0]

    item = 'some_nevr'
    subject = create_test_subject('koji_build', item)
    results = DummyResultsRetriever(subject, 'sometest', 'FAILED')

    decision = policy.check('fedora-rawhide', subject, results)
    assert len(decision) == 1
    assert isinstance(decision[0], TestResultFailed)

    waivers = [{
        'id': 1,
        'subject_identifier': item,
        'subject_type': 'koji_build',
        'testcase': 'sometest',
        'product_version': 'fedora-rawhide',
        'waived': True,
    }]
    decision = policy.check('fedora-rawhide', subject, results)
    decision = waive_answers(decision, waivers)
    assert len(decision) == 1
    assert isinstance(decision[0], RuleSatisfied)


def test_waive_bodhi_update(tmpdir):
    """ Ensure that a koji_build waiver can match a brew-build result

    Note that 'brew-build' in the result does not match 'koji_build' in the
    waiver.  Even though these are different strings, this should work.
    """

    p = tmpdir.join('fedora.yaml')
    p.write(dedent("""
        --- !Policy
        id: some_id
        product_versions:
        - fedora-rawhide
        decision_context: test
        subject_type: bodhi_update
        rules:
          - !PassingTestCaseRule {test_case_name: sometest}
        """))
    policies = load_policies(tmpdir.strpath)
    policy = policies[0]

    item = 'some_bodhi_update'
    subject = create_test_subject('bodhi_update', item)
    results = DummyResultsRetriever(subject, 'sometest', 'FAILED')

    decision = policy.check('fedora-rawhide', subject, results)
    assert len(decision) == 1
    assert isinstance(decision[0], TestResultFailed)

    waivers = [{
        'id': 1,
        'subject_identifier': item,
        'subject_type': 'bodhi_update',
        'testcase': 'sometest',
        'product_version': 'fedora-rawhide',
        'waived': True,
    }]
    decision = policy.check('fedora-rawhide', subject, results)
    decision = waive_answers(decision, waivers)
    assert len(decision) == 1
    assert isinstance(decision[0], RuleSatisfied)


def test_load_policies():
    app = create_app('greenwave.config.TestingConfig')
    assert len(app.config['policies']) > 0
    assert any(policy.id == 'taskotron_release_critical_tasks'
               for policy in app.config['policies'])
    assert any(policy.decision_context == 'bodhi_update_push_stable'
               for policy in app.config['policies'])
    assert any(policy.all_decision_contexts == ['bodhi_update_push_stable']
               for policy in app.config['policies'])
    assert any(getattr(rule, 'test_case_name', None) == 'dist.rpmdeplint'
               for policy in app.config['policies'] for rule in policy.rules)


def test_misconfigured_policies(tmpdir):
    p = tmpdir.join('fedora.yaml')
    p.write(dedent("""
        ---
        id: "taskotron_release_critical_tasks"
        product_versions:
          - fedora-26
        decision_context: bodhi_update_push_stable
        subject_type: bodhi_update
        rules:
          - !PassingTestCaseRule {test_case_name: dist.abicheck}
        """))
    expected_error = "Missing !Policy tag"
    with pytest.raises(SafeYAMLError, match=expected_error):
        load_policies(tmpdir.strpath)


def test_misconfigured_policy_rules(tmpdir):
    p = tmpdir.join('fedora.yaml')
    p.write(dedent("""
        --- !Policy
        id: "taskotron_release_critical_tasks"
        product_versions:
          - fedora-26
        decision_context: bodhi_update_push_stable
        subject_type: bodhi_update
        rules:
          - {test_case_name: dist.abicheck}
        """))
    expected_error = (
        "Policy 'taskotron_release_critical_tasks': "
        "Attribute 'rules': "
        "Expected list of Rule objects"
    )
    with pytest.raises(SafeYAMLError, match=expected_error):
        load_policies(tmpdir.strpath)


def test_passing_testcasename_with_scenario(tmpdir):
    p = tmpdir.join('fedora.yaml')
    p.write(dedent("""
        --- !Policy
        id: "rawhide_compose_sync_to_mirrors"
        product_versions:
          - fedora-rawhide
        decision_context: rawhide_compose_sync_to_mirrors
        subject_type: compose
        rules:
          - !PassingTestCaseRule {test_case_name: compose.install_default_upload,
          scenario: somescenario}
        """))
    load_policies(tmpdir.strpath)


@pytest.mark.parametrize(('product_version', 'applies'), [
    ('fedora-27', True),
    ('fedora-28', True),
    ('epel-7', False),
])
def test_product_versions_pattern(product_version, applies, tmpdir):
    p = tmpdir.join('fedora.yaml')
    p.write(dedent("""
        --- !Policy
        id: dummy_policy
        product_versions:
          - fedora-*
        decision_context: dummy_context
        subject_type: bodhi_update
        rules:
          - !PassingTestCaseRule {test_case_name: test}
        """))
    policies = load_policies(tmpdir.strpath)
    policy = policies[0]

    assert applies == policy.matches(
        decision_context='dummy_context',
        product_version=product_version,
        subject_type='bodhi_update')


@pytest.mark.parametrize('namespace', ["rpms", ""])
def test_remote_rule_policy(tmpdir, namespace):
    """ Testing the RemoteRule with the koji interaction.
    In this case we are just mocking koji """

    subject = create_test_subject('koji_build', 'nethack-1.2.3-1.el9000')

    serverside_fragment = dedent("""
        --- !Policy
        id: "taskotron_release_critical_tasks_with_remoterule"
        product_versions:
          - fedora-26
        decision_context: bodhi_update_push_stable_with_remoterule
        subject_type: koji_build
        rules:
          - !RemoteRule {}
        """)

    remote_fragment = dedent("""
        --- !Policy
        id: "some-policy-from-a-random-packager"
        product_versions:
          - fedora-26
        decision_context: bodhi_update_push_stable_with_remoterule
        rules:
        - !PassingTestCaseRule {test_case_name: dist.upgradepath}
        """)

    p = tmpdir.join('gating.yaml')
    p.write(serverside_fragment)
    app = create_app('greenwave.config.TestingConfig')
    with app.app_context():
        with mock.patch('greenwave.resources.retrieve_scm_from_koji') as scm:
            scm.return_value = (namespace, 'nethack', 'c3c47a08a66451cb9686c49f040776ed35a0d1bb')
            with mock.patch('greenwave.resources.retrieve_yaml_remote_rule') as f:
                f.return_value = remote_fragment
                policies = load_policies(tmpdir.strpath)
                policy = policies[0]

                # Ensure that presence of a result is success.
                results = DummyResultsRetriever(subject, 'dist.upgradepath')
                decision = policy.check('fedora-26', subject, results)
                assert len(decision) == 1
                assert isinstance(decision[0], RuleSatisfied)

                # Ensure that absence of a result is failure.
                results = DummyResultsRetriever()
                decision = policy.check('fedora-26', subject, results)
                assert len(decision) == 1
                assert isinstance(decision[0], TestResultMissing)

                # And that a result with a failure, is a failure.
                results = DummyResultsRetriever(subject, 'dist.upgradepath', 'FAILED')
                decision = policy.check('fedora-26', subject, results)
                assert len(decision) == 1
                assert isinstance(decision[0], TestResultFailed)
                f.assert_called_with(
                    'https://src.fedoraproject.org/{0}'.format(
                        '' if not namespace else namespace + '/'
                    ) + 'nethack/raw/c3c47a08a66451cb9686c49f040776ed35a0d1bb/f/gating.yaml'
                )


def test_remote_rule_policy_old_config(tmpdir):
    """ Testing the RemoteRule with the koji interaction.
    In this case we are just mocking koji """

    subject = create_test_subject('koji_build', 'nethack-1.2.3-1.el9000')

    serverside_fragment = dedent("""
        --- !Policy
        id: "taskotron_release_critical_tasks_with_remoterule"
        product_versions:
          - fedora-26
        decision_context: bodhi_update_push_stable_with_remoterule
        subject_type: koji_build
        rules:
          - !RemoteRule {}
        """)

    remote_fragment = dedent("""
        --- !Policy
        id: "some-policy-from-a-random-packager"
        product_versions:
          - fedora-26
        decision_context: bodhi_update_push_stable_with_remoterule
        rules:
        - !PassingTestCaseRule {test_case_name: dist.upgradepath}
        """)

    p = tmpdir.join('gating.yaml')
    p.write(serverside_fragment)

    config_remote_rules_backup = Config.REMOTE_RULE_POLICIES

    try:
        delattr(Config, 'REMOTE_RULE_POLICIES')

        config = TestingConfig()
        config.DIST_GIT_BASE_URL = 'http://localhost.localdomain/'
        config.DIST_GIT_URL_TEMPLATE = '{DIST_GIT_BASE_URL}{pkg_name}/{rev}/gating.yaml'

        app = create_app(config)

        with app.app_context():
            with mock.patch('greenwave.resources.retrieve_scm_from_koji') as scm:
                scm.return_value = (
                    'rpms', 'nethack', 'c3c47a08a66451cb9686c49f040776ed35a0d1bb'
                )
                with mock.patch('greenwave.resources.retrieve_yaml_remote_rule') as f:
                    f.return_value = remote_fragment
                    policies = load_policies(tmpdir.strpath)
                    policy = policies[0]

                    # Ensure that presence of a result is success.
                    results = DummyResultsRetriever(subject, 'dist.upgradepath')
                    decision = policy.check('fedora-26', subject, results)
                    assert len(decision) == 1
                    assert isinstance(decision[0], RuleSatisfied)

                    f.assert_called_once_with(
                        'http://localhost.localdomain/nethack/'
                        'c3c47a08a66451cb9686c49f040776ed35a0d1bb/gating.yaml'
                    )
    finally:
        Config.REMOTE_RULE_POLICIES = config_remote_rules_backup


def test_remote_rule_policy_brew_build_group(tmpdir):
    """ Testing the RemoteRule with the koji interaction.
    In this case we are just mocking koji """

    subject = create_test_subject(
        'brew-build-group',
        'sha256:0f41e56a1c32519e189ddbcb01d2551e861bd74e603d01769ef5f70d4b30a2dd'
    )
    namespace = 'rpms'

    serverside_fragment = dedent("""
        --- !Policy
        id: "taskotron_release_critical_tasks_with_remoterule"
        product_versions:
          - fedora-26
        decision_context: bodhi_update_push_stable_with_remoterule
        subject_type: brew-build-group
        rules:
          - !RemoteRule {}
        """)

    remote_fragment = dedent("""
        --- !Policy
        id: "some-policy-from-a-random-packager"
        product_versions:
          - fedora-26
        decision_context: bodhi_update_push_stable_with_remoterule
        rules:
        - !PassingTestCaseRule {test_case_name: dist.upgradepath}
        """)

    p = tmpdir.join('gating.yaml')
    p.write(serverside_fragment)
    app = create_app('greenwave.config.TestingConfig')
    with app.app_context():
        with mock.patch('greenwave.resources.retrieve_scm_from_koji') as scm:
            scm.return_value = (namespace, 'nethack', 'c3c47a08a66451cb9686c49f040776ed35a0d1bb')
            with mock.patch('greenwave.resources.retrieve_yaml_remote_rule') as f:
                f.return_value = remote_fragment
                policies = load_policies(tmpdir.strpath)
                policy = policies[0]

                # Ensure that presence of a result is success.
                results = DummyResultsRetriever(subject, 'dist.upgradepath')
                decision = policy.check('fedora-26', subject, results)
                assert len(decision) == 1
                assert isinstance(decision[0], RuleSatisfied)

                # Ensure that absence of a result is failure.
                results = DummyResultsRetriever()
                decision = policy.check('fedora-26', subject, results)
                assert len(decision) == 1
                assert isinstance(decision[0], TestResultMissing)

                # And that a result with a failure, is a failure.
                results = DummyResultsRetriever(subject, 'dist.upgradepath', 'FAILED')
                decision = policy.check('fedora-26', subject, results)
                assert len(decision) == 1
                assert isinstance(decision[0], TestResultFailed)
                f.assert_called_with(
                    'https://git.example.com/devops/greenwave-policies/side-tags/raw/'
                    'master/0f41e56a1c32519e189ddbcb01d2551e861bd74e603d01769ef5f70d4b30a2dd.yaml'
                )
            scm.assert_not_called()


def test_remote_rule_policy_with_no_remote_rule_policies_param_defined(tmpdir):
    """ Testing the RemoteRule with the koji interaction.
    But this time let's assume that REMOTE_RULE_POLICIES is not defined. """

    subject = create_test_subject('koji_build', 'nethack-1.2.3-1.el9000')

    serverside_fragment = dedent("""
        --- !Policy
        id: "taskotron_release_critical_tasks_with_remoterule"
        product_versions:
          - fedora-26
        decision_context: bodhi_update_push_stable_with_remoterule
        subject_type: koji_build
        rules:
          - !RemoteRule {}
        """)

    remote_fragment = dedent("""
        --- !Policy
        id: "some-policy-from-a-random-packager"
        product_versions:
          - fedora-26
        decision_context: bodhi_update_push_stable_with_remoterule
        rules:
        - !PassingTestCaseRule {test_case_name: dist.upgradepath}
        """)

    p = tmpdir.join('gating.yaml')
    p.write(serverside_fragment)
    app = create_app('greenwave.config.FedoraTestingConfig')

    with app.app_context():
        with mock.patch('greenwave.resources.retrieve_scm_from_koji') as scm:
            scm.return_value = ('rpms', 'nethack', 'c3c47a08a66451cb9686c49f040776ed35a0d1bb')
            with mock.patch('greenwave.resources.retrieve_yaml_remote_rule') as f:
                f.return_value = remote_fragment
                policies = load_policies(tmpdir.strpath)
                policy = policies[0]

                # Ensure that presence of a result is success.
                results = DummyResultsRetriever(subject, 'dist.upgradepath')
                decision = policy.check('fedora-26', subject, results)
                assert len(decision) == 1
                assert isinstance(decision[0], RuleSatisfied)
                f.assert_called_with(
                    'https://src.fedoraproject.org/rpms/nethack/raw/'
                    'c3c47a08a66451cb9686c49f040776ed35a0d1bb/f/gating.yaml'
                )


@pytest.mark.parametrize('namespace', ["modules", ""])
def test_remote_rule_policy_redhat_module(tmpdir, namespace):
    """ Testing the RemoteRule with the koji interaction.
    In this case we are just mocking koji """

    nvr = '389-ds-1.4-820181127205924.9edba152'
    subject = create_test_subject('redhat-module', nvr)

    serverside_fragment = dedent("""
        --- !Policy
        id: "taskotron_release_critical_tasks_with_remoterule"
        product_versions:
          - rhel-8
        decision_context: osci_compose_gate
        subject_type: redhat-module
        rules:
          - !RemoteRule {}
        """)

    remote_fragment = dedent("""
        --- !Policy
        product_versions:
          - rhel-8
        decision_context: osci_compose_gate
        subject_type: redhat-module
        rules:
          - !PassingTestCaseRule {test_case_name: baseos-ci.redhat-module.tier0.functional}

        """)

    p = tmpdir.join('gating.yaml')
    p.write(serverside_fragment)
    app = create_app('greenwave.config.TestingConfig')
    with app.app_context():
        with mock.patch('greenwave.resources.retrieve_scm_from_koji') as scm:
            scm.return_value = (namespace, '389-ds', 'c3c47a08a66451cb9686c49f040776ed35a0d1bb')
            with mock.patch('greenwave.resources.retrieve_yaml_remote_rule') as f:
                f.return_value = remote_fragment
                policies = load_policies(tmpdir.strpath)
                policy = policies[0]

                # Ensure that presence of a result is success.
                results = DummyResultsRetriever(subject, 'baseos-ci.redhat-module.tier0.functional')
                decision = policy.check('rhel-8', subject, results)
                assert len(decision) == 1
                assert isinstance(decision[0], RuleSatisfied)

                # Ensure that absence of a result is failure.
                results = DummyResultsRetriever(subject)
                decision = policy.check('rhel-8', subject, results)
                assert len(decision) == 1
                assert isinstance(decision[0], TestResultMissing)

                # And that a result with a failure, is a failure.
                results = DummyResultsRetriever(
                    subject, 'baseos-ci.redhat-module.tier0.functional', 'FAILED')
                decision = policy.check('rhel-8', subject, results)
                assert len(decision) == 1
                assert isinstance(decision[0], TestResultFailed)


def test_remote_rule_policy_redhat_container_image(tmpdir):
    """ Testing the RemoteRule with the koji interaction.
    In this case we are just mocking koji """

    nvr = '389-ds-1.4-820181127205924.9edba152'
    subject = create_test_subject('redhat-container-image', nvr)

    serverside_fragment = dedent("""
        --- !Policy
        id: "taskotron_release_critical_tasks_with_remoterule"
        product_versions:
          - rhel-8
        decision_context: osci_compose_gate
        subject_type: redhat-container-image
        rules:
          - !RemoteRule {}
        """)

    remote_fragment = dedent("""
        --- !Policy
        product_versions:
          - rhel-8
        decision_context: osci_compose_gate
        subject_type: redhat-container-image
        rules:
          - !PassingTestCaseRule {test_case_name: baseos-ci.redhat-container-image.tier0.functional}

        """)

    p = tmpdir.join('gating.yaml')
    p.write(serverside_fragment)
    app = create_app('greenwave.config.TestingConfig')
    with app.app_context():
        with mock.patch('greenwave.resources.retrieve_scm_from_koji') as scm:
            scm.return_value = ('containers', '389-ds', 'c3c47a08a66451cb9686c49f040776ed35a0d1bb')
            with mock.patch('greenwave.resources.retrieve_yaml_remote_rule') as f:
                f.return_value = remote_fragment
                policies = load_policies(tmpdir.strpath)
                policy = policies[0]

                # Ensure that presence of a result is success.
                results = DummyResultsRetriever(
                    subject, 'baseos-ci.redhat-container-image.tier0.functional')
                decision = policy.check('rhel-8', subject, results)
                assert len(decision) == 1
                assert isinstance(decision[0], RuleSatisfied)

                # Ensure that absence of a result is failure.
                results = DummyResultsRetriever(subject)
                decision = policy.check('rhel-8', subject, results)
                assert len(decision) == 1
                assert isinstance(decision[0], TestResultMissing)

                # And that a result with a failure, is a failure.
                results = DummyResultsRetriever(
                    subject, 'baseos-ci.redhat-container-image.tier0.functional', 'FAILED')
                decision = policy.check('rhel-8', subject, results)
                assert len(decision) == 1
                assert isinstance(decision[0], TestResultFailed)


def test_redhat_container_image_subject_type():
    nvr = '389-ds-1.4-820181127205924.9edba152'
    rdb_url = 'http://results.db'
    cur_time = time.strftime('%Y-%m-%dT%H:%M:%S.00')
    testcase_name = 'testcase1'
    app = create_app('greenwave.config.TestingConfig')
    with app.app_context():
        rh_img_subject = create_subject('redhat-container-image', nvr)
        retriever = ResultsRetriever(ignore_ids=list(), when=cur_time, url=rdb_url)
        with mock.patch('requests.Session.get') as req_get:
            req_get.json.return_value = {'data': {'item': [nvr]}}
            retriever._retrieve_all(rh_img_subject, testcase_name)  # pylint: disable=W0212
            assert req_get.call_count == 2
            assert req_get.call_args_list[0] == mock.call(
                f'{rdb_url}/results/latest',
                params={'nvr': nvr,
                        'type': 'redhat-container-image',
                        '_distinct_on': 'scenario,system_architecture,system_variant',
                        'since': f'1900-01-01T00:00:00.000000,{cur_time}',
                        'testcases': testcase_name}
            )
            assert req_get.call_args_list[1] == mock.call(
                f'{rdb_url}/results/latest',
                params={'item': nvr,
                        'type': 'koji_build',
                        '_distinct_on': 'scenario,system_architecture,system_variant',
                        'since': f'1900-01-01T00:00:00.000000,{cur_time}',
                        'testcases': testcase_name}
            )


def test_remote_rule_policy_optional_id(tmpdir):
    subject = create_test_subject('koji_build', 'nethack-1.2.3-1.el9000')

    serverside_fragment = dedent("""
        --- !Policy
        id: "taskotron_release_critical_tasks_with_remoterule"
        product_versions:
          - fedora-26
        decision_context: bodhi_update_push_stable_with_remoterule
        subject_type: koji_build
        rules:
          - !RemoteRule {}
        """)

    remote_fragment = dedent("""
        --- !Policy
        decision_context: bodhi_update_push_stable_with_remoterule
        rules:
          - !PassingTestCaseRule {test_case_name: dist.upgradepath}
        """)

    p = tmpdir.join('gating.yaml')
    p.write(serverside_fragment)
    app = create_app('greenwave.config.TestingConfig')
    with app.app_context():
        with mock.patch('greenwave.resources.retrieve_scm_from_koji') as scm:
            scm.return_value = ('rpms', 'nethack', 'c3c47a08a66451cb9686c49f040776ed35a0d1bb')
            with mock.patch('greenwave.resources.retrieve_yaml_remote_rule') as f:
                f.return_value = remote_fragment
                policies = load_policies(tmpdir.strpath)
                policy = policies[0]

                results = DummyResultsRetriever()
                decision = policy.check('fedora-26', subject, results)
                assert len(decision) == 1
                assert isinstance(decision[0], TestResultMissing)
                assert decision[0].is_satisfied is False


def test_remote_rule_malformed_yaml(tmpdir):
    """ Testing the RemoteRule with a malformed gating.yaml file """

    subject = create_test_subject('koji_build', 'nethack-1.2.3-1.el9000')

    serverside_fragment = dedent("""
        --- !Policy
        id: "taskotron_release_critical_tasks_with_remoterule"
        product_versions:
          - fedora-26
        decision_context: bodhi_update_push_stable_with_remoterule
        subject_type: koji_build
        rules:
          - !RemoteRule {}
        """)

    remote_fragments = [dedent("""
        --- !Policy
           : "some-policy-from-a-random-packager"
        product_versions:
          - fedora-26
        decision_context: bodhi_update_push_stable_with_remoterule
        blacklist: []
        rules:
          - !PassingTestCaseRule {test_case_name: dist.upgradepath}
        """), dedent("""
        --- !Policy
        id: "some-policy-from-a-random-packager"
        product_versions:
          - fedora-26
        decision_context: bodhi_update_push_stable_with_remoterule
        rules:
          - !RemoteRule {test_case_name: dist.upgradepath}
        """)]

    for remote_fragment in remote_fragments:
        p = tmpdir.join('gating.yaml')
        p.write(serverside_fragment)
        app = create_app('greenwave.config.TestingConfig')
        with app.app_context():
            with mock.patch('greenwave.resources.retrieve_scm_from_koji') as scm:
                scm.return_value = ('rpms', 'nethack', 'c3c47a08a66451cb9686c49f040776ed35a0d1bb')
                with mock.patch('greenwave.resources.retrieve_yaml_remote_rule') as f:
                    f.return_value = remote_fragment
                    policies = load_policies(tmpdir.strpath)
                    policy = policies[0]

                    results = DummyResultsRetriever()
                    decision = policy.check('fedora-26', subject, results)
                    assert len(decision) == 1
                    assert isinstance(decision[0], InvalidRemoteRuleYaml)
                    assert decision[0].is_satisfied is False


def test_remote_rule_malformed_yaml_with_waiver(tmpdir):
    """ Testing the RemoteRule with a malformed gating.yaml file
    But this time waiving the error """

    subject = create_test_subject('koji_build', 'nethack-1.2.3-1.el9000')

    serverside_fragment = dedent("""
        --- !Policy
        id: "taskotron_release_critical_tasks_with_remoterule"
        product_versions:
          - fedora-26
        decision_context: bodhi_update_push_stable_with_remoterule
        subject_type: koji_build
        rules:
          - !RemoteRule {}
        """)

    remote_fragments = [dedent("""
        --- !Policy
           : "some-policy-from-a-random-packager"
        product_versions:
          - fedora-26
        decision_context: bodhi_update_push_stable_with_remoterule
        blacklist: []
        rules:
          - !PassingTestCaseRule {test_case_name: dist.upgradepath}
        """), dedent("""
        --- !Policy
        id: "some-policy-from-a-random-packager"
        product_versions:
          - fedora-26
        decision_context: bodhi_update_push_stable_with_remoterule
        rules:
          - !RemoteRule {test_case_name: dist.upgradepath}
        """)]

    for remote_fragment in remote_fragments:
        p = tmpdir.join('gating.yaml')
        p.write(serverside_fragment)
        app = create_app('greenwave.config.TestingConfig')
        with app.app_context():
            with mock.patch('greenwave.resources.retrieve_scm_from_koji') as scm:
                scm.return_value = ('rpms', 'nethack', 'c3c47a08a66451cb9686c49f040776ed35a0d1bb')
                with mock.patch('greenwave.resources.retrieve_yaml_remote_rule') as f:
                    f.return_value = remote_fragment
                    policies = load_policies(tmpdir.strpath)
                    policy = policies[0]

                    results = DummyResultsRetriever()
                    waivers = [{
                        'id': 1,
                        'subject_type': 'koji_build',
                        'subject_identifier': 'nethack-1.2.3-1.el9000',
                        'subject': {'type': 'koji_build', 'item': 'nethack-1.2.3-1.el9000'},
                        'testcase': 'invalid-gating-yaml',
                        'product_version': 'fedora-26',
                        'comment': 'Waiving the invalid gating.yaml file',
                        'waived': True,
                    }]
                    decision = policy.check('fedora-26', subject, results)
                    decision = waive_answers(decision, waivers)
                    assert len(decision) == 0


def test_remote_rule_required():
    """ Testing the RemoteRule with required flag set """
    subject = create_test_subject('koji_build', 'nethack-1.2.3-1.el9000')
    app = create_app('greenwave.config.TestingConfig')
    with app.app_context():
        with mock.patch('greenwave.resources.retrieve_scm_from_koji') as scm:
            scm.return_value = ('rpms', 'nethack', 'c3c47a08a66451cb9686c49f040776ed35a0d1bb')
            with mock.patch('greenwave.resources.retrieve_yaml_remote_rule') as f:
                f.return_value = None
                policies = Policy.safe_load_all(dedent("""
                    --- !Policy
                    id: test
                    product_versions: [fedora-rawhide]
                    decision_context: test
                    subject_type: koji_build
                    rules:
                      - !RemoteRule {required: true}
                """))
                policy = policies[0]
                results = DummyResultsRetriever()
                decision = policy.check('fedora-rawhide', subject, results)
                assert len(decision) == 1
                assert isinstance(decision[0], MissingRemoteRuleYaml)
                assert not decision[0].is_satisfied
                assert decision[0].subject.identifier == subject.identifier


def test_parse_policies_missing_tag():
    expected_error = "Missing !Policy tag"
    with pytest.raises(SafeYAMLError, match=expected_error):
        Policy.safe_load_all("""---""")


def test_parse_policies_unexpected_type():
    policies = dedent("""
        --- !Policy
        42
    """)
    expected_error = "Expected mapping for !Policy tagged object"
    with pytest.raises(SafeYAMLError, match=expected_error):
        RemotePolicy.safe_load_all(policies)


def test_parse_policies_missing_id():
    expected_error = "Policy 'untitled': Attribute 'id' is required"
    with pytest.raises(SafeYAMLError, match=expected_error):
        Policy.safe_load_all(dedent("""
            --- !Policy
            product_versions: [fedora-rawhide]
            decision_context: test
            subject_type: compose
            blacklist: []
            rules:
              - !PassingTestCaseRule {test_case_name: compose.cloud.all}
        """))


def test_parse_policies_missing_product_versions():
    expected_error = "Policy 'test': Attribute 'product_versions' is required"
    with pytest.raises(SafeYAMLError, match=expected_error):
        Policy.safe_load_all(dedent("""
            --- !Policy
            id: test
            decision_context: test
            subject_type: compose
            blacklist: []
            rules:
              - !PassingTestCaseRule {test_case_name: compose.cloud.all}
        """))


def test_parse_policies_missing_decision_context():
    expected_error = "No decision contexts provided"
    with pytest.raises(SafeYAMLError, match=expected_error):
        Policy.safe_load_all(dedent("""
            --- !Policy
            id: test
            product_versions: [fedora-rawhide]
            subject_type: compose
            blacklist: []
            rules:
              - !PassingTestCaseRule {test_case_name: compose.cloud.all}
        """))


def test_parse_policies_both_decision_contexts_set():
    expected_error = 'Both properties "decision_contexts" and "decision_context" were set'
    with pytest.raises(SafeYAMLError, match=expected_error):
        Policy.safe_load_all(dedent("""
            --- !Policy
            id: test
            product_versions: [fedora-rawhide]
            subject_type: compose
            blacklist: []
            decision_context: test1
            decision_contexts:
            - test1
            - test2
            rules:
              - !PassingTestCaseRule {test_case_name: compose.cloud.all}
        """))


def test_policy_with_arbitrary_subject_type(tmpdir):
    p = tmpdir.join('fedora.yaml')
    p.write(dedent("""
        --- !Policy
        id: "some_policy"
        product_versions:
          - rhel-9000
        decision_context: bodhi_update_push_stable
        subject_type: kind-of-magic
        rules:
          - !PassingTestCaseRule {test_case_name: sometest}
        """))
    policies = load_policies(tmpdir.strpath)
    policy = policies[0]

    subject = create_test_subject('kind-of-magic', 'nethack-1.2.3-1.el9000')
    results = DummyResultsRetriever(subject, 'sometest', 'PASSED')
    decision = policy.check('rhel-9000', subject, results)
    assert len(decision) == 1
    assert isinstance(decision[0], TestResultPassed)


def test_policy_all_decision_contexts(tmpdir):
    p = tmpdir.join('fedora.yaml')
    p.write(dedent("""
        --- !Policy
        id: "some_policy1"
        product_versions:
          - rhel-9000
        decision_contexts:
          - test1
          - test2
          - test3
        subject_type: kind-of-magic
        rules:
          - !PassingTestCaseRule {test_case_name: sometest}

        --- !Policy
        id: "some_policy2"
        product_versions:
          - rhel-9000
        decision_context: test4
        subject_type: kind-of-magic
        rules:
          - !PassingTestCaseRule {test_case_name: sometest}
        """))
    policies = load_policies(tmpdir.strpath)
    policy = policies[0]
    assert len(policy.all_decision_contexts) == 3
    assert set(policy.all_decision_contexts) == {'test1', 'test2', 'test3'}
    policy = policies[1]
    assert len(policy.all_decision_contexts) == 1
    assert policy.all_decision_contexts == ['test4']


@pytest.mark.parametrize(('package', 'num_decisions'), [
    ('nethack', 1),
    ('net*', 1),
    ('python-requests', 0),
])
def test_policy_with_packages_whitelist(tmpdir, package, num_decisions):
    p = tmpdir.join('temp.yaml')
    p.write(dedent("""
        --- !Policy
        id: "some_policy"
        product_versions:
          - rhel-9000
        decision_context: test
        subject_type: koji_build
        packages:
        - {}
        rules:
          - !PassingTestCaseRule {{test_case_name: sometest}}
        """.format(package)))
    policies = load_policies(tmpdir.strpath)
    policy = policies[0]

    subject = create_test_subject('koji_build', 'nethack-1.2.3-1.el9000')
    results = DummyResultsRetriever(subject, 'sometest', 'PASSED')
    decision = policy.check('rhel-9000', subject, results)
    assert len(decision) == num_decisions
    if num_decisions:
        assert isinstance(decision[0], TestResultPassed)


def test_parse_policies_invalid_rule():
    expected_error = "Policy 'test': Attribute 'rules': Expected list of Rule objects"
    with pytest.raises(SafeYAMLError, match=expected_error):
        Policy.safe_load_all(dedent("""
            --- !Policy
            id: test
            product_versions: [fedora-rawhide]
            decision_context: test
            subject_type: compose
            blacklist: []
            rules:
              - !PassingTestCaseRule {test_case_name: compose.cloud.all}
              - bad_rule
        """))


def test_parse_policies_remote_missing_tag():
    expected_error = "Missing !Policy tag"
    with pytest.raises(SafeYAMLError, match=expected_error):
        RemotePolicy.safe_load_all("""---""")


def test_parse_policies_remote_missing_id_is_ok():
    policies = RemotePolicy.safe_load_all(dedent("""
        --- !Policy
        product_versions: [fedora-rawhide]
        decision_context: test
        subject_type: koji_build
        rules:
          - !PassingTestCaseRule {test_case_name: test.case.name}
    """))
    assert len(policies) == 1
    assert policies[0].id is None


def test_parse_policies_remote_missing_subject_type_is_ok():
    policies = RemotePolicy.safe_load_all(dedent("""
        --- !Policy
        product_versions: [fedora-rawhide]
        decision_context: test
        rules:
          - !PassingTestCaseRule {test_case_name: test.case.name}
    """))
    assert len(policies) == 1
    assert policies[0].subject_type == 'koji_build'


def test_parse_policies_remote_recursive():
    expected_error = "Policy 'test': RemoteRule is not allowed in remote policies"
    with pytest.raises(SafeYAMLError, match=expected_error):
        RemotePolicy.safe_load_all(dedent("""
            --- !Policy
            id: test
            product_versions: [fedora-rawhide]
            decision_context: bodhi_update_push_stable_with_remoterule
            subject_type: koji_build
            rules:
              - !RemoteRule {}
        """))


def test_parse_policies_remote_multiple():
    policies = RemotePolicy.safe_load_all(dedent("""
        --- !Policy
        id: test1
        product_versions: [fedora-rawhide]
        decision_context: test
        rules:
          - !PassingTestCaseRule {test_case_name: test.case.name}

        --- !Policy
        id: test2
        product_versions: [fedora-rawhide]
        decision_context: test
        rules:
          - !PassingTestCaseRule {test_case_name: test.case.name}
    """))
    assert len(policies) == 2
    assert policies[0].id == 'test1'
    assert policies[1].id == 'test2'


def test_parse_policies_remote_subject_types():
    policies = RemotePolicy.safe_load_all(dedent("""
        --- !Policy
        id: test1
        product_versions: [fedora-rawhide]
        decision_context: test
        subject_type: koji_build
        rules:
          - !PassingTestCaseRule {test_case_name: test.case.name}

        --- !Policy
        id: test2
        product_versions: [fedora-rawhide]
        decision_context: test
        subject_type: redhat-module
        rules:
          - !PassingTestCaseRule {test_case_name: test.case.name}
    """))
    assert len(policies) == 2
    assert policies[0].id == 'test1'
    assert policies[0].subject_type == 'koji_build'
    assert policies[1].id == 'test2'
    assert policies[1].subject_type == 'redhat-module'


def test_parse_policies_remote_multiple_missing_tag():
    expected_error = "Missing !Policy tag"
    with pytest.raises(SafeYAMLError, match=expected_error):
        RemotePolicy.safe_load_all(dedent("""
            --- !Policy
            id: test1
            product_versions: [fedora-rawhide]
            decision_context: test
            rules:
              - !PassingTestCaseRule {test_case_name: test.case.name}

            ---
            id: test2
            product_versions: [fedora-rawhide]
            decision_context: test
            rules:
              - !PassingTestCaseRule {test_case_name: test.case.name}
        """))


def test_parse_policies_remote_missing_rule_attribute():
    expected_error = (
        "Policy 'test': "
        "Attribute 'rules': "
        "YAML object !PassingTestCaseRule: "
        "Attribute 'test_case_name' is required"
    )
    with pytest.raises(SafeYAMLError, match=expected_error):
        RemotePolicy.safe_load_all(dedent("""
            --- !Policy
            id: test
            product_versions: [fedora-rawhide]
            decision_context: test
            rules:
              - !PassingTestCaseRule {test_case: test.case.name}
        """))


def test_policies_to_json():
    policies = Policy.safe_load_all(dedent("""
        --- !Policy
        id: test
        product_versions: [fedora-rawhide]
        decision_context: test
        subject_type: compose
        blacklist: []
        excluded_packages: []
        rules: []
    """))
    assert len(policies) == 1
    assert policies[0].to_json() == {
        'id': 'test',
        'product_versions': ['fedora-rawhide'],
        'decision_context': 'test',
        'decision_contexts': [],
        'subject_type': 'compose',
        'blacklist': [],
        'excluded_packages': [],
        'packages': [],
        'rules': [],
        'relevance_key': None,
        'relevance_value': None,
    }


def test_policy_with_subject_type_component_version(tmpdir):
    nv = '389-ds-base-1.4.0.10'
    subject = create_test_subject('component-version', nv)
    p = tmpdir.join('fedora.yaml')
    p.write(dedent("""
        --- !Policy
        id: "test-new-subject-type"
        product_versions:
        - fedora-29
        decision_context: decision_context_test_component_version
        subject_type: component-version
        blacklist: []
        rules:
          - !PassingTestCaseRule {test_case_name: test_for_new_type}
        """))
    policies = load_policies(tmpdir.strpath)
    policy = policies[0]
    results = DummyResultsRetriever(subject, 'test_for_new_type', 'PASSED')
    decision = policy.check('fedora-29', subject, results)
    assert len(decision) == 1
    assert isinstance(decision[0], RuleSatisfied)


@pytest.mark.parametrize('subject_type', ["redhat-module", "redhat-container-image"])
def test_policy_with_subject_type_redhat_module(tmpdir, subject_type):
    nsvc = 'httpd:2.4:20181018085700:9edba152'
    subject = create_test_subject(subject_type, nsvc)
    p = tmpdir.join('fedora.yaml')
    p.write(dedent("""
        --- !Policy
        id: "test-new-subject-type"
        product_versions:
        - fedora-29
        decision_context: decision_context_test_redhat_module
        subject_type: %s
        blacklist: []
        rules:
          - !PassingTestCaseRule {test_case_name: test_for_redhat_module_type}
        """ % subject_type))
    policies = load_policies(tmpdir.strpath)
    policy = policies[0]
    results = DummyResultsRetriever(subject, 'test_for_redhat_module_type', 'PASSED')
    decision = policy.check('fedora-29', subject, results)
    assert len(decision) == 1
    assert isinstance(decision[0], RuleSatisfied)


@pytest.mark.parametrize('namespace', ["rpms", ""])
def test_remote_rule_policy_on_demand_policy(namespace):
    """ Testing the RemoteRule with the koji interaction when on_demand policy is given.
    In this case we are just mocking koji """

    nvr = 'nethack-1.2.3-1.el9000'
    subject = create_test_subject('koji_build', nvr)

    serverside_json = {
        'product_version': 'fedora-26',
        'id': 'taskotron_release_critical_tasks_with_remoterule',
        'subject_type': 'koji_build',
        'subject_identifier': nvr,
        'rules': [
            {
                'type': 'RemoteRule'
            },
        ],
    }

    remote_fragment = dedent("""
        --- !Policy
        id: "some-policy-from-a-random-packager"
        decision_context: bodhi_update_push_stable_with_remoterule
        rules:
        - !PassingTestCaseRule {test_case_name: dist.upgradepath}
        """)

    app = create_app('greenwave.config.TestingConfig')
    with app.app_context():
        with mock.patch('greenwave.resources.retrieve_scm_from_koji') as scm:
            scm.return_value = (namespace, 'nethack', 'c3c47a08a66451cb9686c49f040776ed35a0d1bb')
            with mock.patch('greenwave.resources.retrieve_yaml_remote_rule') as f:
                f.return_value = remote_fragment
                policy = OnDemandPolicy.create_from_json(serverside_json)

                # Ensure that presence of a result is success.
                results = DummyResultsRetriever(subject, 'dist.upgradepath')
                decision = policy.check('fedora-26', subject, results)
                assert len(decision) == 1
                assert isinstance(decision[0], RuleSatisfied)

                # Ensure that absence of a result is failure.
                results = DummyResultsRetriever()
                decision = policy.check('fedora-26', subject, results)
                assert len(decision) == 1
                assert isinstance(decision[0], TestResultMissing)

                # And that a result with a failure, is a failure.
                results = DummyResultsRetriever(subject, 'dist.upgradepath', 'FAILED')
                decision = policy.check('fedora-26', subject, results)
                assert len(decision) == 1
                assert isinstance(decision[0], TestResultFailed)


@pytest.mark.parametrize('two_rules', (True, False))
def test_on_demand_policy_match(two_rules):
    """ Proceed other rules when there's no source URL in Koji build """

    nvr = 'httpd-2.4.el9000'
    subject = create_test_subject('koji_build', nvr)

    serverside_json = {
        'product_version': 'fedora-30',
        'id': 'taskotron_release_critical_tasks_with_remoterule',
        'subject_type': 'koji_build',
        'subject_identifier': nvr,
        'rules': [
            {
                'type': 'RemoteRule'
            }
        ],
    }

    if two_rules:
        serverside_json['rules'].append({
            "type": "PassingTestCaseRule",
            "test_case_name": "fake.testcase.tier0.validation"
        })

    app = create_app('greenwave.config.TestingConfig')
    with app.app_context():
        with mock.patch('greenwave.resources.get_server_proxy') as koji_server:
            koji_server_instance = mock.MagicMock()
            koji_server_instance.getBuild.return_value = {'extra': {'source': None}}
            koji_server.return_value = koji_server_instance
            policy = OnDemandPolicy.create_from_json(serverside_json)

            policy_matches = policy.matches(subject=subject)

            koji_server_instance.getBuild.assert_called_once()
            assert policy_matches

            results = DummyResultsRetriever(
                subject, 'fake.testcase.tier0.validation', 'PASSED'
            )
            decision = policy.check('fedora-30', subject, results)
            if two_rules:
                assert len(decision) == 1
                assert isinstance(decision[0], RuleSatisfied)


def test_remote_rule_policy_on_demand_policy_required():
    """ Testing the RemoteRule with the koji interaction when on_demand policy is given.
    In this case we are just mocking koji """

    nvr = 'nethack-1.2.3-1.el9000'
    subject = create_test_subject('koji_build', nvr)

    serverside_json = {
        'product_version': 'fedora-26',
        'id': 'taskotron_release_critical_tasks_with_remoterule',
        'subject': [{'item': nvr, 'type': 'koji_build'}],
        'rules': [
            {
                'type': 'RemoteRule',
                'required': True
            },
        ],
    }

    app = create_app('greenwave.config.TestingConfig')
    with app.app_context():
        with mock.patch('greenwave.resources.retrieve_scm_from_koji') as scm:
            scm.return_value = ('rpms', 'nethack', 'c3c47a08a66451cb9686c49f040776ed35a0d1bb')
            with mock.patch('greenwave.resources.retrieve_yaml_remote_rule') as f:
                f.return_value = None

                policy = OnDemandPolicy.create_from_json(serverside_json)
                assert len(policy.rules) == 1
                assert isinstance(policy.rules[0], RemoteRule)
                assert policy.rules[0].required

                results = DummyResultsRetriever()
                decision = policy.check('fedora-26', subject, results)
                assert len(decision) == 1
                assert isinstance(decision[0], MissingRemoteRuleYaml)
                assert not decision[0].is_satisfied
                assert decision[0].subject.identifier == subject.identifier


def test_two_rules_no_duplicate(tmpdir):
    nvr = 'nethack-1.2.3-1.el9000'
    subject = create_test_subject('koji_build', nvr)

    serverside_fragment = dedent("""
        --- !Policy
        id: "taskotron_release_critical_tasks_with_remoterule"
        product_versions:
          - fedora-31
        decision_context: bodhi_update_push_stable_with_remoterule
        subject_type: koji_build
        rules:
          - !RemoteRule {}
          - !PassingTestCaseRule {test_case_name: dist.upgradepath}
        """)

    remote_fragment = dedent("""
        --- !Policy
        id: "some-policy-from-a-random-packager"
        product_versions:
          - fedora-31
        decision_context: bodhi_update_push_stable_with_remoterule
        rules:
          - !PassingTestCaseRule {test_case_name: dist.upgradepath}
        """)

    p = tmpdir.join('gating.yaml')
    p.write(serverside_fragment)
    app = create_app('greenwave.config.TestingConfig')
    with app.app_context():
        with mock.patch('greenwave.resources.retrieve_scm_from_koji') as scm:
            scm.return_value = ('rmps', 'nethack', 'c3c47a08a66451cb9686c49f040776ed35a0d1bb')
            with mock.patch('greenwave.resources.retrieve_yaml_remote_rule') as f:
                f.return_value = remote_fragment
                policies = load_policies(tmpdir.strpath)
                policy = policies[0]

                # Ensure that presence of a result is success.
                results = DummyResultsRetriever(subject, 'dist.upgradepath')
                decision = policy.check('fedora-31', subject, results)
                assert len(decision) == 1
                assert isinstance(decision[0], RuleSatisfied)

                # Ensure that absence of a result is failure.
                results = DummyResultsRetriever()
                decision = policy.check('fedora-31', subject, results)
                assert len(decision) == 1
                assert isinstance(decision[0], TestResultMissing)

                # And that a result with a failure, is a failure.
                results = DummyResultsRetriever(subject, 'dist.upgradepath', 'FAILED')
                decision = policy.check('fedora-31', subject, results)
                assert len(decision) == 1
                assert isinstance(decision[0], TestResultFailed)


def test_cache_all_results_temporarily():
    """
    All results are stored in temporary cache (valid during single request).
    """
    subject = create_test_subject('bodhi_update', 'update-1')
    results = DummyResultsRetriever(subject, 'sometest', 'FAILED')

    retrieved = results.retrieve(subject, testcase=None)
    assert results.retrieve_data_called == 1
    assert retrieved

    cached = results.retrieve(subject, testcase='sometest')
    assert results.retrieve_data_called == 1
    assert cached == retrieved


def test_cache_passing_results():
    """
    Passing results are stored in external cache because it's not expected that
    the outcome changes once they passed.
    """
    subject = create_test_subject('bodhi_update', 'update-1')
    results = DummyResultsRetriever(subject, 'sometest', 'FAILED')

    retrieved = results.retrieve(subject, testcase=None)
    assert results.retrieve_data_called == 1
    assert retrieved

    results2 = DummyResultsRetriever(subject, 'sometest', 'PASSED')
    results2.external_cache = results.external_cache
    retrieved2 = results2.retrieve(subject, testcase='sometest')
    assert results2.retrieve_data_called == 1
    assert retrieved2
    assert retrieved2 != retrieved

    # Result stays PASSED even if the latest is now FAILED.
    results3 = DummyResultsRetriever(subject, 'sometest', 'FAILED')
    results3.external_cache = results.external_cache
    cached = results3.retrieve(subject, testcase='sometest')
    assert results3.retrieve_data_called == 0
    assert cached == retrieved2
