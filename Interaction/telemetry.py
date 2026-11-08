"""Read-only publisher for an existing SDK owner; no CAN ownership or actuation."""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from .sdk_adapter import sample_connected_arm


class RelayError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward the controller credential to a redirected destination.
        return None


class TelemetryPublisher:
    def __init__(self, base_url, token, *, host, side, serial, calibration_id=None,
                 clock=time.time, timeout=.2):
        url = urllib.parse.urlsplit(base_url)
        if (url.scheme != 'https' or not url.hostname or url.username or url.password
                or url.query or url.fragment or url.path not in ('', '/')):
            raise ValueError('An HTTPS relay origin without credentials or path is required')
        if side not in ('left', 'right') or not isinstance(serial, str) or not serial:
            raise ValueError('Explicit arm side and adapter serial required')
        if not token or '\n' in token or '\r' in token:
            raise ValueError('Controller credential required')
        if not 0 < timeout <= .25:
            raise ValueError('Bounded telemetry timeout required')
        self.base_url = base_url.rstrip('/')
        self._token = token
        self.host, self.side, self.serial = host, side, serial
        self.calibration_id, self.clock, self.timeout = calibration_id, clock, timeout
        self.session_id = None
        self.opener = urllib.request.build_opener(NoRedirect)

    def _post(self, path, payload):
        req = urllib.request.Request(self.base_url + path,
            data=json.dumps(payload, allow_nan=False).encode(),
            headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self._token})
        try:
            with self.opener.open(req, timeout=self.timeout) as response:
                result = json.loads(response.read(65536))
        except urllib.error.HTTPError as error:
            raise RelayError('Telemetry relay rejected request (HTTP %d)' % error.code) from None
        except (OSError, ValueError) as error:
            raise RelayError('Telemetry delivery failed; no retry or motor action performed') from None
        if not isinstance(result, dict):
            raise RelayError('Invalid telemetry relay response')
        return result

    def register(self):
        """Explicit registration only; never automatically clear a stop or replace a lease."""
        response = self._post('/api/interaction/controller/session',
                              {'host': self.host, 'side': self.side})
        nonce = response.get('session_id')
        if response.get('table') != 10 or not isinstance(nonce, str) or not re.fullmatch('[0-9a-f]{32}', nonce):
            raise RelayError('Invalid Table 10 registration')
        self.session_id = nonce

    def publish_once(self, already_connected_arm):
        """Sample now and deliver once. Call outside the motor-control loop.

        Network failures leave hardware lifecycle entirely with the existing SDK
        owner. No reconnect, target, enable, disable, release or disconnect calls.
        A stop response must be handled by that owner's guarded lifecycle before
        any future physical-output integration; this publisher cannot actuate.
        """
        if self.session_id is None:
            raise RelayError('Explicit registration required')
        sample = sample_connected_arm(already_connected_arm, self.clock(),
            serial=self.serial, side=self.side, calibration_id=self.calibration_id,
            local_deadman=False)
        response = self._post('/api/interaction/controller/state',
            {**sample, 'session_id': self.session_id, 'mode': 'monitor'})
        if response.get('ok') is not True or type(response.get('stop_requested')) is not bool:
            raise RelayError('Invalid telemetry acknowledgement')
        return {'delivered': True, 'stop_requested': response['stop_requested'],
                'feedback_unix': sample['feedback_unix'], 'physical_output_enabled': False}
