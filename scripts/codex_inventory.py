#!/usr/bin/env python3
"""Query a fresh Codex app-server without invoking a model or changing trust."""
import argparse
import json
import os
import queue
import subprocess
import threading
from pathlib import Path


class AppServer:
    def __init__(self, binary, cwd, home=None):
        env = dict(os.environ)
        if home:
            env['CODEX_HOME'] = str(home)
        self.process = subprocess.Popen([binary, 'app-server', '--stdio'], cwd=cwd,
                                        env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self.events = queue.Queue()
        self.seq = 0
        self.notifications = []
        threading.Thread(target=self._read, daemon=True).start()
        self.call('initialize', {'clientInfo': {'name': 'harness-acceptance', 'version': '3.1.0'},
                                 'capabilities': {'experimentalApi': True}})
        self.send({'method': 'initialized'})

    def _read(self):
        for line in self.process.stdout:
            try:
                self.events.put(json.loads(line))
            except ValueError:
                pass

    def send(self, message):
        self.process.stdin.write(json.dumps(message) + '\n')
        self.process.stdin.flush()

    def call(self, method, params, timeout=60):
        self.seq += 1
        expected = self.seq
        self.send({'id': expected, 'method': method, 'params': params})
        import time
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            item = self.events.get(timeout=max(.01, deadline - time.monotonic()))
            if item.get('id') == expected:
                if 'error' in item:
                    raise RuntimeError(item['error'])
                return item.get('result')
            self.notifications.append(item)
        raise TimeoutError(method)

    def close(self):
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('repos', nargs='+')
    parser.add_argument('--binary', default='/usr/lib/chatgpt/resources/codex')
    parser.add_argument('--home')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    client = AppServer(args.binary, args.repos[0], args.home)
    try:
        result = {'binary': args.binary, 'status': 'PASS',
                  'skills': client.call('skills/list', {'cwds': args.repos, 'forceReload': True}),
                  'hooks': client.call('hooks/list', {'cwds': args.repos}),
                  'plugins': client.call('plugin/list', {'cwds': args.repos})}
        Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({'status': result['status'], 'output': args.output}))
    finally:
        client.close()


if __name__ == '__main__':
    main()
