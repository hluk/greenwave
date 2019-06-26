# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0+
"""
The "waiverdb handler".

This module is responsible for listening new waivers from WaiverDB. When a new
waiver is received, Greenwave will check all applicable policies for that waiver,
and if the new waiver causes the decision to change it will publish a message
to the message bus about the newly satisfied/unsatisfied policy.
"""

import logging
import json

import fedmsg.consumers
import requests

import greenwave.app_factory
from greenwave.api_v1 import subject_type_identifier_to_list
from greenwave.monitor import (
    publish_decision_exceptions_waiver_counter,
    messaging_tx_to_send_counter, messaging_tx_stopped_counter,
    messaging_tx_sent_ok_counter, messaging_tx_failed_counter)
from greenwave.policies import applicable_decision_context_product_version_pairs
from greenwave.utils import right_before_this_time

try:
    import fedora_messaging.api
    import fedora_messaging.exceptions
except ImportError:
    pass


requests_session = requests.Session()


log = logging.getLogger(__name__)


class WaiverDBHandler(fedmsg.consumers.FedmsgConsumer):
    """
    Handle a new waiver.

    Attributes:
        topic (list): A list of strings that indicate which fedmsg topics this consumer listens to.
    """

    config_key = 'waiverdb_handler'

    def __init__(self, hub, *args, **kwargs):
        """
        Initialize the WaiverDBHandler, subscribing it to the appropriate topics.

        Args:
            hub (moksha.hub.hub.CentralMokshaHub): The hub from which this handler is consuming
                messages. It is used to look up the hub config.
        """

        prefix = hub.config.get('topic_prefix')
        env = hub.config.get('environment')
        suffix = hub.config.get('waiverdb_topic_suffix', 'waiver.new')
        self.topic = ['.'.join([prefix, env, suffix])]
        self.fedmsg_config = fedmsg.config.load_config()

        config = kwargs.pop('config', None)

        super(WaiverDBHandler, self).__init__(hub, *args, **kwargs)

        self.flask_app = greenwave.app_factory.create_app(config)
        self.greenwave_api_url = self.flask_app.config['GREENWAVE_API_URL']
        log.info('Greenwave waiverdb handler listening on: %s', self.topic)

    def consume(self, message):
        """
        Process the given message and publish a message if the decision is changed.

        Args:
            message (munch.Munch): A fedmsg about a new waiver.
        """
        message = message.get('body', message)
        log.debug('Processing message "%s"', message)
        msg = message['msg']

        product_version = msg['product_version']
        testcase = msg['testcase']
        subject_type = msg['subject_type']
        subject_identifier = msg['subject_identifier']
        submit_time = msg['timestamp']

        with self.flask_app.app_context():
            self._publish_decision_changes(subject_type, subject_identifier, submit_time,
                                           product_version, testcase)

    @publish_decision_exceptions_waiver_counter.count_exceptions()
    def _publish_decision_changes(self, subject_type, subject_identifier, submit_time,
                                  product_version, testcase):
        policies = self.flask_app.config['policies']
        contexts_product_versions = applicable_decision_context_product_version_pairs(
            policies,
            subject_type=subject_type,
            subject_identifier=subject_identifier,
            testcase=testcase,
            product_version=product_version)

        for decision_context, product_version in sorted(contexts_product_versions):
            messaging_tx_to_send_counter.labels(handler='waiverdb').inc()
            data = {
                'decision_context': decision_context,
                'product_version': product_version,
                'subject_type': subject_type,
                'subject_identifier': subject_identifier,
            }
            response = requests_session.post(
                self.greenwave_api_url + '/decision',
                headers={'Content-Type': 'application/json'},
                data=json.dumps(data))

            if not response.ok:
                log.error(response.text)
                messaging_tx_stopped_counter.labels(handler='waiverdb').inc()
                continue

            decision = response.json()

            # get old decision
            data.update({
                'when': right_before_this_time(submit_time),
            })
            response = requests_session.post(
                self.greenwave_api_url + '/decision',
                headers={'Content-Type': 'application/json'},
                data=json.dumps(data))

            if not response.ok:
                log.error(response.text)
                messaging_tx_stopped_counter.labels(handler='waiverdb').inc()
                continue

            old_decision = response.json()

            if decision == old_decision:
                log.debug('Skipped emitting fedmsg, decision did not change: %s', decision)
                messaging_tx_stopped_counter.labels(handler='waiverdb').inc()
                continue

            msg = decision
            decision.update({
                'subject_type': subject_type,
                'subject_identifier': subject_identifier,
                # subject is for backwards compatibility only:
                'subject': subject_type_identifier_to_list(subject_type,
                                                           subject_identifier),
                'testcase': testcase,
                'decision_context': decision_context,
                'product_version': product_version,
                'previous': old_decision,
            })
            log.info(
                'Emitting a message on the bus, %r, with the topic '
                '"greenwave.decision.update"', decision)
            if self.flask_app.config['MESSAGING'] == 'fedmsg':
                log.debug('  - to fedmsg')
                try:
                    fedmsg.publish(topic='decision.update', msg=msg)
                    messaging_tx_sent_ok_counter.labels(handler='waiverdb').inc()
                except Exception:
                    messaging_tx_failed_counter.labels(handler='waiverdb').inc()
                    raise
            elif self.flask_app.config['MESSAGING'] == 'fedora-messaging':
                log.debug('  - to fedora-messaging')
                try:
                    msg = fedora_messaging.api.Message(
                        topic='greenwave.decision.update',
                        body=msg
                    )
                    fedora_messaging.api.publish(msg)
                    messaging_tx_sent_ok_counter.labels(handler='waiverdb').inc()
                except fedora_messaging.exceptions.PublishReturned as e:
                    log.warning(
                        'Fedora Messaging broker rejected message %s: %s',
                        msg.id, e)
                    messaging_tx_stopped_counter.labels(handler='waiverdb').inc()
                except fedora_messaging.exceptions.ConnectionException as e:
                    log.warning('Error sending message %s: %s', msg.id, e)
                    messaging_tx_failed_counter.labels(handler='waiverdb').inc()
                except Exception:  # pylint: disable=broad-except
                    log.exception('Error sending fedora-messaging message')
                    messaging_tx_failed_counter.labels(handler='waiverdb').inc()

            messaging_tx_stopped_counter.labels(handler='waiverdb').inc()
