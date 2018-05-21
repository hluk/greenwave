# SPDX-License-Identifier: GPL-2.0+
""" Greenwave resources.

This module contains routines for interacting with other services (resultsdb,
waiverdb, etc..).

"""

import json
import requests
import urllib3.exceptions

import urlparse
import xmlrpclib
from flask import current_app
from werkzeug.exceptions import BadGateway

from greenwave.cache import cached
import greenwave.utils
import greenwave.policies

requests_session = requests.Session()


@cached
@greenwave.utils.retry(wait_on=urllib3.exceptions.NewConnectionError)
def retrieve_rev_from_koji(nvr):
    """ Retrieve cached rev from koji using the nrv """
    proxy = xmlrpclib.ServerProxy(current_app.config['KOJI_BASE_URL'])
    build = proxy.getBuild(nvr)

    if not build:
        raise BadGateway("Found %s when looking for %s at %s" % (
            build, nvr, current_app.config['KOJI_BASE_URL']))

    try:
        url = urlparse.urlparse(build['extra']['source']['original_url'])
        if not url.scheme.startswith('git'):
            raise BadGateway('Error occurred looking for the "rev" in koji.')
        return url.fragment
    except Exception:
        raise BadGateway('Error occurred looking for the "rev" in koji.')


@cached
def retrieve_yaml_remote_original_spec_nvr_rule(rev, pkg_name):
    """ Retrieve cached gating.yaml content for a given rev. """
    data = {
        "DIST_GIT_BASE_URL": current_app.config['DIST_GIT_BASE_URL'],
        "pkg_name": pkg_name,
        "rev": rev
    }
    url = current_app.config['DIST_GIT_URL_TEMPLATE'].format(**data)
    response = requests_session.request('HEAD', url,
                                        headers={'Content-Type': 'application/json'},
                                        timeout=60)
    if response.status_code == 404:
        return greenwave.policies.RuleSatisfied()
    elif response.status_code != 200:
        raise BadGateway('Error occurred looking for gating.yaml file in the dist-git repo.')

    # gating.yaml found...
    response = requests_session.request('GET', url,
                                        headers={'Content-Type': 'application/json'},
                                        timeout=60)
    response.raise_for_status()
    return response.content


def retrieve_builds_in_update(update_id):
    """
    Queries Bodhi to find the list of builds in the given update.
    Returns a list of build NVRs.
    """
    update_info_url = urlparse.urljoin(current_app.config['BODHI_URL'],
                                       '/updates/{}'.format(update_id))
    timeout = current_app.config['REQUESTS_TIMEOUT']
    verify = current_app.config['REQUESTS_VERIFY']
    response = requests_session.get(update_info_url,
                                    headers={'Accept': 'application/json'},
                                    timeout=timeout, verify=verify)
    response.raise_for_status()
    return [build['nvr'] for build in response.json()['update']['builds']]


def retrieve_update_for_build(nvr):
    """
    Queries Bodhi to find the update which the given build is in (if any).
    Returns a Bodhi updateid, or None if the build is not in any update.
    """
    updates_list_url = urlparse.urljoin(current_app.config['BODHI_URL'], '/updates/')
    params = {'builds': nvr}
    timeout = current_app.config['REQUESTS_TIMEOUT']
    verify = current_app.config['REQUESTS_VERIFY']
    response = requests_session.get(updates_list_url,
                                    params=params,
                                    headers={'Accept': 'application/json'},
                                    timeout=timeout, verify=verify)
    response.raise_for_status()
    matching_updates = response.json()['updates']
    if matching_updates:
        return matching_updates[0]['updateid']
    return None


def retrieve_item_results(item):
    """ Retrieve cached results from resultsdb for a given item. """
    # XXX make this more efficient than just fetching everything

    params = item.copy()
    params.update({'limit': '1000'})
    timeout = current_app.config['REQUESTS_TIMEOUT']
    verify = current_app.config['REQUESTS_VERIFY']
    response = requests_session.get(
        current_app.config['RESULTSDB_API_URL'] + '/results',
        params=params, verify=verify, timeout=timeout)
    response.raise_for_status()
    return response.json()['data']


@cached
def retrieve_results(subject_type, subject_identifier):
    """
    Returns all results from ResultsDB which might be relevant for the given
    decision subject, accounting for all the different possible ways in which
    test results can be reported.
    """
    # Note that the reverse of this logic also lives in the
    # announcement_subjects() method of the Resultsdb consumer (it has to map
    # from a newly received result back to the possible subjects it is for).
    results = []
    if subject_type == 'bodhi_update':
        results.extend(retrieve_item_results(
            {u'type': u'bodhi_update', u'item': subject_identifier}))
    elif subject_type == 'koji_build':
        results.extend(retrieve_item_results({u'type': u'koji_build', u'item': subject_identifier}))
        results.extend(retrieve_item_results({u'type': u'brew-build', u'item': subject_identifier}))
        results.extend(retrieve_item_results({u'original_spec_nvr': subject_identifier}))
    elif subject_type == 'compose':
        results.extend(retrieve_item_results({u'productmd.compose.id': subject_identifier}))
    else:
        raise RuntimeError('Unhandled subject type %r' % subject_type)
    return results


# NOTE - not cached, for now.
@greenwave.utils.retry(wait_on=urllib3.exceptions.NewConnectionError)
def retrieve_waivers(product_version, subject_type, subject_identifier):
    timeout = current_app.config['REQUESTS_TIMEOUT']
    verify = current_app.config['REQUESTS_VERIFY']
    filters = [{
        'product_version': product_version,
        'subject_type': subject_type,
        'subject_identifier': subject_identifier,
    }]
    response = requests_session.post(
        current_app.config['WAIVERDB_API_URL'] + '/waivers/+filtered',
        headers={'Content-Type': 'application/json'},
        data=json.dumps({'filters': filters}),
        verify=verify,
        timeout=timeout)
    response.raise_for_status()
    return response.json()['data']


# NOTE - not cached.
@greenwave.utils.retry(timeout=300, interval=30, wait_on=urllib3.exceptions.NewConnectionError)
def retrieve_decision(greenwave_url, data):
    timeout = current_app.config['REQUESTS_TIMEOUT']
    verify = current_app.config['REQUESTS_VERIFY']
    headers = {'Content-Type': 'application/json'}
    response = requests_session.post(greenwave_url, headers=headers, data=json.dumps(data),
                                     timeout=timeout, verify=verify)
    response.raise_for_status()
    return response.json()
