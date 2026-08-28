import json
import unittest

import context
import gqlclient

KEY = 'unraid-api-key-not-a-real-one-0123456789'


class TestBuildRequest(unittest.TestCase):
    def test_url_and_method(self):
        req = gqlclient.build_request('192.168.2.19', 29220, KEY, '{ info { id } }')
        self.assertEqual('https://192.168.2.19:29220/graphql', req.full_url)
        self.assertEqual('POST', req.get_method())

    def test_key_goes_in_the_x_api_key_header(self):
        req = gqlclient.build_request('h', 1, KEY, '{}')
        self.assertEqual(KEY, req.get_header('X-api-key'))

    def test_body_is_a_graphql_envelope(self):
        req = gqlclient.build_request('h', 1, KEY, '{ info { id } }')
        self.assertEqual({'query': '{ info { id } }'}, json.loads(req.data.decode('utf-8')))

    def test_the_key_is_not_in_the_body(self):
        req = gqlclient.build_request('h', 1, KEY, '{ info { id } }')
        self.assertNotIn(KEY, req.data.decode('utf-8'))


class TestScrub(unittest.TestCase):
    def test_replaces_every_occurrence(self):
        self.assertEqual('a <redacted> b <redacted>',
                         gqlclient.scrub('a %s b %s' % (KEY, KEY), KEY))

    def test_none_and_empty_secret_are_no_ops(self):
        self.assertEqual('text', gqlclient.scrub('text', None))
        self.assertEqual('text', gqlclient.scrub('text', ''))


class TestParseResponse(unittest.TestCase):
    def load(self, name):
        return context.fixture('seed/' + name).encode('utf-8')

    def test_good_response_returns_the_data_object(self):
        data = gqlclient.parse_response(200, self.load('info.json'))
        self.assertEqual('Golem', data['info']['os']['hostname'])

    def test_resolver_error_raises_domain_error_with_the_message(self):
        with self.assertRaises(gqlclient.DomainError) as ctx:
            gqlclient.parse_response(200, self.load('error_resolver.json'))
        self.assertIn('battery', str(ctx.exception))

    def test_504_html_body_raises_transport_error_naming_the_gateway_timeout(self):
        with self.assertRaises(gqlclient.TransportError) as ctx:
            gqlclient.parse_response(504, self.load('error_504.html'))
        self.assertIn('504', str(ctx.exception))

    def test_the_504_message_carries_no_markup(self):
        """This string is rendered to the operator, verbatim, on the Disks tab.

        Seen on Raven during the P1 exit trial: a stale label read

          Golem: showing the disk list collected 2026-08-28, 5:52:49 PM EDT -
          the latest poll did not complete (HTTP 504 Gateway Time-out from
          nginx - the query took longer than the server allows (<html>
          <head><title>504 Gateway Time-out</title></head> <body> <center><h1>
          504 Gateway Time-out</h1></center> <hr><cente)).

        The sentence already says what happened; 120 characters of nginx's
        boilerplate, cut off mid-tag, only made it unreadable.
        """
        with self.assertRaises(gqlclient.TransportError) as ctx:
            gqlclient.parse_response(504, self.load('error_504.html'))
        message = str(ctx.exception)
        self.assertNotIn('<', message)
        self.assertIn('the query took longer than the server allows', message)

    def test_an_unrecognised_non_json_body_still_shows_what_came_back(self):
        # The opposite case: here the snippet is the ONLY clue about what the
        # peer actually sent, so it stays.
        with self.assertRaises(gqlclient.TransportError) as ctx:
            gqlclient.parse_response(500, b'a totally unexpected thing')
        self.assertIn('a totally unexpected thing', str(ctx.exception))

    def test_malformed_json_raises_transport_error(self):
        with self.assertRaises(gqlclient.TransportError):
            gqlclient.parse_response(200, self.load('error_malformed.txt'))

    def test_401_raises_auth_error(self):
        with self.assertRaises(gqlclient.AuthError):
            gqlclient.parse_response(401, b'{"errors":[{"message":"Unauthorized"}]}')

    def test_403_raises_auth_error(self):
        with self.assertRaises(gqlclient.AuthError):
            gqlclient.parse_response(403, b'{"errors":[{"message":"Forbidden"}]}')

    def test_graphql_unauthenticated_code_raises_auth_error_even_on_200(self):
        # The API answers 200 with an UNAUTHENTICATED extension for a key that
        # lacks the scope. That is a bad-key answer, not a broken resolver.
        body = json.dumps({'data': None, 'errors': [
            {'message': 'Access denied', 'extensions': {'code': 'UNAUTHENTICATED'}}]}).encode()
        with self.assertRaises(gqlclient.AuthError):
            gqlclient.parse_response(200, body)

    def test_auth_error_is_a_transport_error(self):
        # Callers that only want "could not read it" catch one exception.
        self.assertTrue(issubclass(gqlclient.AuthError, gqlclient.TransportError))

    def test_data_null_with_no_errors_raises_domain_error(self):
        with self.assertRaises(gqlclient.DomainError):
            gqlclient.parse_response(200, b'{"data":null}')

    def test_empty_body_raises_transport_error(self):
        with self.assertRaises(gqlclient.TransportError):
            gqlclient.parse_response(200, b'')

    def test_partial_data_with_errors_still_raises(self):
        # Constraint 1: one failing resolver nulls the response. We never keep
        # half an answer and call the domain ok.
        body = json.dumps({'data': {'array': None},
                           'errors': [{'message': 'boom'}]}).encode()
        with self.assertRaises(gqlclient.DomainError):
            gqlclient.parse_response(200, body)


class TestPostIsInjectable(unittest.TestCase):
    def test_post_uses_the_opener_it_is_given(self):
        class FakeResponse:
            status = 200
            def read(self):
                return b'{"data":{"info":{"id":"x"}}}'
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        calls = []

        class FakeOpener:
            def open(self, req, timeout=None):
                calls.append((req.full_url, timeout))
                return FakeResponse()

        data = gqlclient.post('h', 1, KEY, '{ info { id } }', timeout=7, opener=FakeOpener())
        self.assertEqual({'info': {'id': 'x'}}, data)
        self.assertEqual([('https://h:1/graphql', 7)], calls)


if __name__ == '__main__':
    unittest.main()
