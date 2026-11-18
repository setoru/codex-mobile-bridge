"""Creation keeps the latest native speed setting without starting a turn."""
import copy
import unittest

import test_session_recovery as fixtures


class RecoveryTierTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.SessionRecoveryTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def recover(self, source):
        fixture = self.fixture
        fixture.bridge.store.history = lambda sid, **kwargs: copy.deepcopy(source) if sid == fixture.source else fixture.history(sid, **kwargs)
        return fixture.bridge.recover(fixture.source, fixture.recovery_body())

    def test_recovery_inherits_speed_from_the_latest_saved_turn(self):
        fixture = self.fixture
        source = fixture.history(fixture.source)
        source['latestThreadSettings'] = {}
        source['turns'] = [
            {'id': 'earlier', 'status': 'completed', 'params': {'serviceTier': 'default'}},
            {'id': 'latest', 'status': 'completed', 'params': {'serviceTier': 'fast'}},
        ]
        result = self.recover(source)
        self.assertEqual(result['status'], 'created', result)
        target = fixture.native.threads[result['id']]
        self.assertEqual(target['serviceTier'], 'fast')
        self.assertEqual(len(target['turns']), 0)
        creation = next(params for method, params in fixture.native.calls if method == 'thread/start')
        self.assertEqual(creation['serviceTier'], 'fast')

    def test_recovery_uses_canonical_turn_order_for_the_latest_speed(self):
        fixture = self.fixture
        source = fixture.history(fixture.source)
        source['latestThreadSettings'] = {}
        earlier = {'id': 'earlier', 'status': 'completed', 'params': {'serviceTier': 'default'}}
        latest = {'id': 'latest', 'status': 'completed', 'params': {'serviceTier': 'fast'}}
        source['turns'] = [earlier]
        source['turnHistory'] = {'kind': 'canonical', 'history': {
            'entitiesByKey': {'latest': latest, 'earlier': earlier},
            'islands': [{'entries': [{'value': 'earlier'}, {'value': 'latest'}, {'value': 'earlier'}]}],
        }}
        result = self.recover(source)
        self.assertEqual(result['status'], 'created', result)
        self.assertEqual(fixture.native.threads[result['id']]['serviceTier'], 'fast')

    def test_explicitly_cleared_speed_overrides_the_last_turn(self):
        fixture = self.fixture
        source = fixture.history(fixture.source)
        source['latestThreadSettings'] = {'serviceTier': None}
        source['turns'] = [{'id': 'last', 'status': 'completed', 'params': {'serviceTier': 'fast'}}]
        result = self.recover(source)
        self.assertEqual(result['status'], 'created', result)
        creation = next(params for method, params in fixture.native.calls if method == 'thread/start')
        self.assertIn('serviceTier', creation)
        self.assertIsNone(creation['serviceTier'])
        self.assertIsNone(fixture.native.threads[result['id']]['serviceTier'])

    def test_absent_speed_in_the_latest_turn_does_not_reuse_an_older_value(self):
        fixture = self.fixture
        source = fixture.history(fixture.source)
        source['latestThreadSettings'] = {}
        source['turns'] = [
            {'id': 'earlier', 'status': 'completed', 'params': {'serviceTier': 'fast'}},
            {'id': 'last', 'status': 'completed', 'params': {'model': 'source-model'}},
        ]
        result = self.recover(source)
        self.assertEqual(result['status'], 'created', result)
        creation = next(params for method, params in fixture.native.calls if method == 'thread/start')
        self.assertNotIn('serviceTier', creation)

    def test_latest_model_and_reasoning_settings_override_older_source_metadata(self):
        fixture = self.fixture
        source = fixture.history(fixture.source)
        source['latestThreadSettings'] = {'model': 'latest-source-model', 'effort': 'xhigh', 'serviceTier': 'fast'}
        result = self.recover(source)
        self.assertEqual(result['status'], 'created', result)
        self.assertTrue(result['ready'], result)
        target = fixture.native.threads[result['id']]
        self.assertEqual((target['model'], target['reasoningEffort'], target['serviceTier']),
                         ('latest-source-model', 'xhigh', 'fast'))
        self.assertEqual(target['turns'], [])

    def test_missing_reasoning_setting_keeps_native_reasoning_default(self):
        fixture = self.fixture
        source = fixture.history(fixture.source)
        source['latestReasoningEffort'] = None
        source['latestThreadSettings'] = {}
        result = self.recover(source)
        self.assertEqual(result['status'], 'created', result)
        self.assertTrue(result['ready'], result)
        target = fixture.native.threads[result['id']]
        self.assertEqual((target['model'], target['reasoningEffort']), ('source-model', 'low'))
        creation = next(params for method, params in fixture.native.calls if method == 'thread/start')
        self.assertNotIn('config', creation)


if __name__ == '__main__':
    unittest.main()
