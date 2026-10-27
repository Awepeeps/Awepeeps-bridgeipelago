import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from modules.room_endpoint import resolve_port, verify_seed
from test_room_sleep_notices import load_tracker_client, FakeConnection
from test_hint_relay import load_hint_client


class EndpointTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            'ExpectedSeedName': 'seed-a', 'ArchipelagoPort': 12345,
            'ArchipelagoServerURL': 'https://host.test/room/room-a/',
        }
        self.response = SimpleNamespace(status_code=200, raise_for_status=Mock(),
                                        json=Mock(return_value={'last_port': 23456}))
        self.get = Mock(return_value=self.response)

    def test_port_refresh_uses_only_read_only_api_and_bounded_timeout(self):
        self.assertEqual(resolve_port(self.config, False, self.get), 23456)
        self.get.assert_called_once_with('https://host.test/api/room_status/room-a',
                                         timeout=10, allow_redirects=False)

    def test_lookup_failure_never_falls_back_to_cached_port(self):
        self.get.side_effect = TimeoutError()
        with self.assertRaises(TimeoutError):
            resolve_port(self.config, False, self.get)

    def test_invalid_api_ports_are_rejected(self):
        for port in (None, True, 0, 65536, 'bad', 12.5):
            self.response.json.return_value = {'last_port': port}
            with self.subTest(port=port), self.assertRaises((ValueError, TypeError)):
                resolve_port(self.config, False, self.get)

    def test_self_host_uses_configured_port_without_http(self):
        self.assertEqual(resolve_port(self.config, True, self.get), 12345)
        self.get.assert_not_called()

    def test_identity_is_explicit_and_missing_or_wrong_identity_is_rejected(self):
        verify_seed(self.config, {'seed_name': 'seed-a'})
        for config, info in (({}, {'seed_name': 'seed-a'}), (self.config, {}),
                             (self.config, {'seed_name': 'seed-b'})):
            with self.subTest(config=config, info=info), self.assertRaises(ValueError):
                verify_seed(config, info)

    def test_api_error_or_redirect_is_rejected(self):
        self.response.raise_for_status.side_effect = RuntimeError('HTTP 500')
        with self.assertRaises(RuntimeError):
            resolve_port(self.config, False, self.get)
        self.response.raise_for_status.side_effect = None
        self.response.status_code = 302
        with self.assertRaises(ValueError):
            resolve_port(self.config, False, self.get)

    def test_tracker_refreshes_before_open_and_skips_socket_if_api_fails(self):
        cls, ns = load_tracker_client()
        ns.update({'resolve_port': resolve_port, 'requests': SimpleNamespace(get=self.get),
                   'connect': Mock(side_effect=ConnectionRefusedError()),
                   'SetConfigVariable': Mock()})
        ns['CoreConfig']['ArchipelagoConfig'].update(self.config)
        client = cls()
        client.start()
        self.assertEqual(ns['connect'].call_args.args[0], 'localhost:23456')
        ns['SetConfigVariable'].assert_called_once_with('ArchipelagoPort', 23456)
        ns['connect'].reset_mock()
        self.get.side_effect = TimeoutError()
        client.start()
        ns['connect'].assert_not_called()

    def test_wrong_seed_never_sends_login_or_overwrites_tracker_cache(self):
        cls, ns = load_tracker_client()
        ns.update({'verify_seed': verify_seed, 'WriteRoomInfo': Mock()})
        ns['CoreConfig']['ArchipelagoConfig'].update(self.config)
        client = cls()
        connection = FakeConnection(json.dumps([{'cmd': 'RoomInfo', 'seed_name': 'seed-b'}]), client)
        connection.send = Mock()
        connection.close = Mock()
        client.ap_connection = connection
        client.check_datapackage = Mock()
        client.run()
        connection.send.assert_not_called()
        connection.close.assert_called_once()
        client.check_datapackage.assert_not_called()
        ns['WriteRoomInfo'].assert_not_called()

    def test_failed_port_save_does_not_connect_or_announce_a_move(self):
        cls, ns = load_tracker_client()
        ns.update({'resolve_port': resolve_port, 'requests': SimpleNamespace(get=self.get),
                   'connect': Mock(), 'SetConfigVariable': Mock(return_value=
                       'Unable to save configuration. No settings were changed.')})
        ns['CoreConfig']['ArchipelagoConfig'].update(self.config)
        cls().start()
        ns['connect'].assert_not_called()
        self.assertEqual(ns['chat_queue'].messages, [])

    def test_matching_seed_allows_tracker_login(self):
        cls, ns = load_tracker_client()
        ns.update({'verify_seed': verify_seed, 'WriteRoomInfo': Mock()})
        ns['CoreConfig']['ArchipelagoConfig'].update(self.config)
        client = cls()
        connection = FakeConnection(json.dumps([{'cmd': 'RoomInfo', 'seed_name': 'seed-a'}]), client)
        connection.send = Mock()
        connection.close = Mock()
        client.ap_connection = connection
        client.check_datapackage = Mock()
        client.run()
        self.assertEqual(json.loads(connection.send.call_args.args[0])[0]['cmd'], 'Connect')

    def test_hint_client_also_checks_identity_before_login(self):
        cls, ns = load_hint_client({})
        ns['verify_seed'] = verify_seed
        ns['CoreConfig']['ArchipelagoConfig'].update(self.config)
        client = cls(slot_name='Player')
        connection = FakeConnection(json.dumps([{'cmd': 'RoomInfo', 'seed_name': 'seed-b'}]), client)
        connection.send = Mock()
        connection.close = Mock()
        client.ap_connection = connection
        client.run()
        connection.send.assert_not_called()
        connection.close.assert_called_once()

    def test_intentional_tracker_stop_does_not_queue_another_restart(self):
        cls, ns = load_tracker_client()
        client = cls()
        def stopped_receive(**kwargs):
            client.is_closed.set()
            raise ns['websockets'].exceptions.ConnectionClosed('local stop')
        client.ap_connection = SimpleNamespace(recv=stopped_receive)
        client.run()
        self.assertEqual(ns['websocket_queue'].messages, [])
        ns['WriteToErrorLog'].assert_not_called()


if __name__ == '__main__':
    unittest.main()
