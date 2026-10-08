"""A local stand-in for the Pushover message API.

``POST /1/messages.json`` checks for ``token``, ``user`` and ``message``
like the real API and records the message; ``GET /_received`` lists the
recorded messages and ``DELETE /_received`` clears them.  Messages are
kept in memory, so run it as a single process.
"""
import json
import uuid

from flask import Flask, Response, request

REQUIRED = ('token', 'user', 'message')
FIELDS = ('token', 'user', 'title', 'message', 'priority')


def _json(data, status=200):
    return Response(json.dumps(data), status=status,
                    mimetype='application/json')


def create_app():
    app = Flask(__name__)
    received = []

    def messages():
        missing = [name for name in REQUIRED if not request.values.get(name)]
        if missing:
            return _json({'status': 0, 'request': str(uuid.uuid4()),
                          'errors': ['%s is missing' % name
                                     for name in missing]}, 400)
        message = dict((name, request.values.get(name)) for name in FIELDS)
        try:
            message['priority'] = int(message['priority'] or 0)
        except ValueError:
            return _json({'status': 0, 'request': str(uuid.uuid4()),
                          'errors': ['priority is invalid']}, 400)
        received.append(message)
        return _json({'status': 1, 'request': str(uuid.uuid4())})

    def list_received():
        return _json(received)

    def clear_received():
        del received[:]
        return Response(status=204)

    app.add_url_rule('/1/messages.json', 'messages', messages,
                     methods=['POST'])
    app.add_url_rule('/_received', 'list_received', list_received)
    app.add_url_rule('/_received', 'clear_received', clear_received,
                     methods=['DELETE'])
    return app


if __name__ == '__main__':
    create_app().run(host='0.0.0.0', port=8025)
