"""Small standard-library client; no ML dependencies needed inside the swarm container."""
import argparse
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def request(method, path, data=None):
    token = Path(os.environ['IZGE_RUNNER_TOKEN_FILE']).read_text().strip()
    url = os.getenv('IZGE_RUNNER_URL', 'http://host.docker.internal:8765').rstrip('/')
    req = Request(url + path, data=json.dumps(data).encode() if data is not None else None, method=method,
                  headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'})
    with urlopen(req, timeout=30) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    submit = commands.add_parser('submit')
    submit.add_argument('config_name')
    submit.add_argument('--overrides', default='{}', help='JSON object with allowlisted dotted keys')
    commands.add_parser('list')
    commands.add_parser('health')
    for name in ('status', 'logs', 'metrics', 'cancel', 'resume'):
        command = commands.add_parser(name)
        command.add_argument('job_id')
        if name == 'logs':
            command.add_argument('--tail', type=int, default=100)
    args = parser.parse_args()
    method, data = 'GET', None
    if args.command == 'submit':
        path, method = '/jobs', 'POST'
        data = dict(config_name=args.config_name, overrides=json.loads(args.overrides))
    elif args.command in ('list', 'health'):
        path = '/jobs' if args.command == 'list' else '/health'
    else:
        if not args.job_id.isalnum():
            parser.error('invalid job id')
        path = f'/jobs/{args.job_id}'
        if args.command != 'status':
            path += f'/{args.command}'
        if args.command == 'logs':
            path += f'?tail={args.tail}'
        if args.command in ('cancel', 'resume'):
            method = 'POST'
    try:
        print(json.dumps(request(method, path, data), indent=2))
    except HTTPError as exc:
        parser.exit(1, f'Runner HTTP {exc.code}\n')


if __name__ == '__main__':
    main()
