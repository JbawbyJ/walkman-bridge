"""Reserve the actual listening socket before announcing it to Electron."""
import hashlib
import hmac
import json
import os
import socket
import uvicorn

def main():
    token = os.environ['NIGHTOPS_TOKEN']
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(128)
        port = listener.getsockname()[1]
        os.environ['NIGHTOPS_ORIGIN'] = f'http://127.0.0.1:{port}'
        proof = hmac.new(token.encode(), str(port).encode(), hashlib.sha256).hexdigest()
        print('NIGHTOPS_READY ' + json.dumps(dict(port=port, proof=proof)), flush=True)
        config = uvicorn.Config('main:create_app', factory=True, host='127.0.0.1', port=port,
                                access_log=False, log_level='warning')
        uvicorn.Server(config).run(sockets=[listener])

if __name__ == '__main__':
    main()
