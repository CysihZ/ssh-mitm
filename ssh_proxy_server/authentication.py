import logging
import re

from enhancements.modules import Module

import paramiko
from ssh_proxy_server.clients.ssh import SSHClient, AuthenticationMethod
from ssh_proxy_server.clients.sftp import SFTPClient
from ssh_proxy_server.exceptions import MissingHostException


class Authenticator(Module):

    AGENT_FORWARDING = False

    @classmethod
    def parser_arguments(cls):
        cls.PARSER.add_argument(
            '--remote-host',
            dest='remote_host',
            help='remote host to connect to'
        )
        cls.PARSER.add_argument(
            '--remote-port',
            dest='remote_port',
            default=22,
            type=int,
            help='remote port to connect to'
        )
        cls.PARSER.add_argument(
            '--auth-username',
            dest='auth_username',
            help='username for remote authentication'
        )
        cls.PARSER.add_argument(
            '--auth-password',
            dest='auth_password',
            help='password for remote authentication'
        )

    def __init__(self, session):
        super().__init__()
        self.session = session

    def get_remote_host_credentials(self, username):
        if self.args.remote_host:
            return (
                self.args.auth_username or username,
                self.args.remote_host,
                self.args.remote_port
            )
        if self.session.proxyserver.transparent:
            return (
                self.args.auth_username or username,
                self.session.socket_remote_address[0],
                self.session.socket_remote_address[1]
            )
        p = r'(?P<username>[^@]+)@(?P<host>[^:]+):?(?P<port>[0-9]*)'
        m = re.search(p, username)
        if m['host']:
            return (
                self.args.auth_username or m['username'],
                m['host'],
                int(m['port']) if m['port'] else self.args.remote_port
            )
        raise ValueError('No remote host')

    def authenticate(self, username=None, password=None, key=None):
        if username:
            user, host, port = self.get_remote_host_credentials(username)
            logging.info('try to connect to %s:%s with %s', host, port, user)
            self.session.username = user
            self.session.remote_address = (host, port)
        if key:
            self.session.key = key

        try:
            if self.session.agent:
                return self.auth_agent(
                    self.session.username,
                    self.session.remote_address[0],
                    self.session.remote_address[1]
                )
            if password:
                return self.auth_password(
                    self.session.username,
                    self.session.remote_address[0],
                    self.session.remote_address[1],
                    self.args.auth_password or password
                )
            if key:
                return self.auth_publickey(
                    self.session.username,
                    self.session.remote_address[0],
                    self.session.remote_address[1],
                    key
                )
        except MissingHostException:
            logging.error("no remote host")
        except Exception:
            logging.exception("internal error, abort authentication!")
        return paramiko.AUTH_FAILED

    def auth_agent(self, username, host, port):
        raise NotImplementedError("authentication must be implemented")

    def auth_password(self, username, host, port, password):
        raise NotImplementedError("authentication must be implemented")

    def auth_publickey(self, username, host, port, key):
        raise NotImplementedError("authentication must be implemented")

    def connect(self, user, host, port, method, password=None, key=None):
        logging.info(
            "Client Verbindung mit folgenden Parametern wird hergestellt: Remote Address: %s; Port: %s; Username: %s; Password: %s; Key: %s; Agent: %s",
            host,
            port,
            user,
            password,
            ('None' if key is None else 'not None'),
            str(self.session.agent)
        )

        if not host:
            raise MissingHostException()

        sshclient = SSHClient(
            host,
            port,
            method,
            password,
            user,
            key,
            self.session
        )
        if sshclient.connect():
            self.session.ssh_client = sshclient
            self.session.sftp_client = SFTPClient.from_client(sshclient)
            return paramiko.AUTH_SUCCESSFUL
        logging.debug('connection failed!')
        return paramiko.AUTH_FAILED


class AuthenticatorPassThrough(Authenticator):

    def auth_agent(self, username, host, port):
        return self.connect(username, host, port, AuthenticationMethod.agent)

    def auth_password(self, username, host, port, password):
        return self.connect(username, host, port, AuthenticationMethod.password, password=password)

    def auth_publickey(self, username, host, port, key):
        if key.can_sign():
            return self.connect(username, host, port, AuthenticationMethod.publickey, key=key)
        if self.AGENT_FORWARDING:
            # Ein Publickey wird nur direkt von check_auth_publickey
            # übergeben. In dem Fall müssen wir den Client authentifizieren,
            # damit wir auf den Agent warten können!
            logging.debug("authentication failed. accept connection and wait for agent.")
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED
