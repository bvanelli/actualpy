# Note: This docstring is also used by this script's command line help.
"""A one-stop helper for desktop app to acquire an authorization code.

It starts a web server to listen redirect_uri, waiting for auth code.
It optionally opens a browser window to guide a human user to manually login.
After obtaining an auth code, the web server will automatically shut down.
"""
import logging
import os
import socket
import sys
import threading
import time
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
from string import Template
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)


"""
The following code is adapted from:

https://github.com/AzureAD/microsoft-authentication-library-for-python/blob/70fd4d1599fc15c876c8eaccd29b9f7ae73fecd6/msal/oauth2cli/authcode.py#L30

The file was left in the original state, so it was not adapted to fit the repository coding style. In the future,
it might be worthwhile to include the library `msal-python` as a real dependency.
"""


def obtain_auth_code(listen_port: int, auth_uri: str = None):
    with AuthCodeReceiver(port=listen_port) as receiver:
        return receiver.get_auth_response(
            auth_uri=auth_uri,
            welcome_template="""<html><body>
                Open this link to <a href='$auth_uri'>Sign In</a>
                (You may want to use incognito window)
                <hr><a href='$abort_uri'>Abort</a>
                </body></html>""",
        ).get("code")


def _is_inside_docker():
    try:
        with open("/proc/1/cgroup") as f:  # https://stackoverflow.com/a/20012536/728675
            # Search keyword "/proc/pid/cgroup" in this link for the file format
            # https://man7.org/linux/man-pages/man7/cgroups.7.html
            for line in f.readlines():
                cgroup_path = line.split(":", 2)[2].strip()
                if cgroup_path.strip() != "/":
                    return True
    except IOError:
        pass  # We are probably not running on Linux
    return os.path.exists("/.dockerenv")  # Docker on Mac will run this line


def is_wsl():
    # "Official" way of detecting WSL: https://github.com/Microsoft/WSL/issues/423#issuecomment-221627364
    # Run `uname -a` to get 'release' without python
    #   - WSL 1: '4.4.0-19041-Microsoft'
    #   - WSL 2: '4.19.128-microsoft-standard'
    import platform

    uname = platform.uname()
    platform_name = getattr(uname, "system", uname[0]).lower()
    release = getattr(uname, "release", uname[2]).lower()
    return platform_name == "linux" and "microsoft" in release


def _browse(auth_uri, browser_name=None):  # throws ImportError, webbrowser.Error
    """Browse uri with named browser. Default browser is customizable by $BROWSER"""
    import webbrowser  # Lazy import. Some distro may not have this.

    if browser_name:
        browser_opened = webbrowser.get(browser_name).open(auth_uri)
    else:
        # This one can survive BROWSER=nonexist, while get(None).open(...) can not
        browser_opened = webbrowser.open(auth_uri)

    # In WSL which doesn't have www-browser, try launching browser with PowerShell
    if not browser_opened and is_wsl():
        try:
            import subprocess

            # https://docs.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_powershell_exe
            # Ampersand (&) should be quoted
            exit_code = subprocess.call(
                ["powershell.exe", "-NoProfile", "-Command", 'Start-Process "{}"'.format(auth_uri)]
            )
            browser_opened = exit_code == 0
        except FileNotFoundError:  # WSL might be too old
            pass
    return browser_opened


def _qs2kv(qs):
    """Flatten parse_qs()'s single-item lists into the item itself"""
    return {k: v[0] if isinstance(v, list) and len(v) == 1 else v for k, v in qs.items()}


def _is_html(text):
    return text.startswith("<")  # Good enough for our purpose


def _escape(key_value_pairs):
    return {k: escape(v) for k, v in key_value_pairs.items()}


def _printify(text):
    # If an https request is sent to an http server, the text needs to be repr-ed
    return repr(text) if isinstance(text, str) and not text.isprintable() else text


class _AuthCodeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        # For flexibility, we choose to not check self.path matching redirect_uri
        # assert self.path.startswith('/THE_PATH_REGISTERED_BY_THE_APP')
        qs = parse_qs(urlparse(self.path).query)
        if qs.get("token") or qs.get("error"):  # So, it is an auth response
            auth_response = _qs2kv(qs)
            logger.debug("Got auth response: %s", auth_response)
            if self.server.auth_state and self.server.auth_state != auth_response.get("state"):
                # OAuth2 successful and error responses contain state when it was used
                # https://www.rfc-editor.org/rfc/rfc6749#section-4.2.2.1
                self._send_full_response("State mismatch")  # Possibly an attack
            else:
                template = self.server.success_template if "token" in qs else self.server.error_template
                if _is_html(template.template):
                    safe_data = _escape(auth_response)  # Foiling an XSS attack
                else:
                    safe_data = auth_response
                self._send_full_response(template.safe_substitute(**safe_data))
                self.server.auth_response = auth_response  # Set it now, after the response is likely sent
        else:
            self._send_full_response(self.server.welcome_page)
        # NOTE: Don't do self.server.shutdown() here. It'll halt the server.

    def _send_full_response(self, body, is_ok=True):
        self.send_response(200 if is_ok else 400)
        content_type = "text/html" if _is_html(body) else "text/plain"
        self.send_header("Content-type", content_type)
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, format, *args):
        # To override the default log-to-stderr behavior
        logger.debug(format, *map(_printify, args))


class _AuthCodeHttpServer(HTTPServer, object):
    def __init__(self, server_address, *args, **kwargs):
        _, port = server_address
        if port and (sys.platform == "win32" or is_wsl()):
            # The default allow_reuse_address is True. It works fine on non-Windows.
            # On Windows, it undesirably allows multiple servers listening on same port,
            # yet the second server would not receive any incoming request.
            # So, we need to turn it off.
            self.allow_reuse_address = False
        super(_AuthCodeHttpServer, self).__init__(server_address, *args, **kwargs)

    def handle_timeout(self):
        # It will be triggered when no request comes in self.timeout seconds.
        # See https://docs.python.org/3/library/socketserver.html#socketserver.BaseServer.handle_timeout
        raise RuntimeError("Timeout. No auth response arrived.")  # Terminates this server
        # We choose to not call self.server_close() here,
        # because it would cause a socket.error exception in handle_request(),
        # and likely end up the server being server_close() twice.


class _AuthCodeHttpServer6(_AuthCodeHttpServer):
    address_family = socket.AF_INET6


class AuthCodeReceiver(object):
    # This class has (rather than is) an _AuthCodeHttpServer, so it does not leak API
    def __init__(self, port=None, scheduled_actions=None):
        """Create a Receiver waiting for incoming auth response.

        :param port:
            The local web server will listen at http://...:<port>
            You need to use the same port when you register with your app.
            If your Identity Provider supports dynamic port, you can use port=0 here.
            Port 0 means to use an arbitrary unused port, per this official example:
            https://docs.python.org/2.7/library/socketserver.html#asynchronous-mixins

        :param scheduled_actions:
            For example, if the input is
            ``[(10, lambda: print("Got stuck during sign in? Call 800-000-0000"))]``
            then the receiver would call that lambda function after
            waiting the response for 10 seconds.
        """
        address = "0.0.0.0" if _is_inside_docker() else "127.0.0.1"  # Hardcode
        # Per RFC 8252 (https://tools.ietf.org/html/rfc8252#section-8.3):
        #   * Clients should listen on the loopback network interface only.
        #     (It is not recommended to use "" shortcut to bind all addr.)
        #   * the use of localhost is NOT RECOMMENDED.
        #     (Use) the loopback IP literal
        #     rather than localhost avoids inadvertently listening on network
        #     interfaces other than the loopback interface.
        # Note:
        #   When this server physically listens to a specific IP (as it should),
        #   you will still be able to specify your redirect_uri using either
        #   IP (e.g. 127.0.0.1) or localhost, whichever matches your registration.
        self._scheduled_actions = sorted(scheduled_actions or [])  # Make a copy
        Server = _AuthCodeHttpServer6 if ":" in address else _AuthCodeHttpServer
        # TODO: But, it would treat "localhost" or "" as IPv4.
        # If pressed, we might just expose a family parameter to caller.
        self._server = Server((address, port or 0), _AuthCodeHandler)
        self._closing = False

    def get_port(self):
        """The port this server actually listening to"""
        # https://docs.python.org/2.7/library/socketserver.html#SocketServer.BaseServer.server_address
        return self._server.server_address[1]

    def get_auth_response(self, timeout=None, **kwargs):
        """Wait and return the auth response. Raise RuntimeError when timeout.

        :param str auth_uri:
            If provided, this function will try to open a local browser.
        :param int timeout: In seconds. None means wait indefinitely.
        :param str state:
            You may provide the state you used in auth_uri,
            then we will use it to validate incoming response.
        :param str welcome_template:
            If provided, your end user will see it instead of the auth_uri.
            When present, it shall be a plaintext or html template following
            `Python Template string syntax <https://docs.python.org/3/library/string.html#template-strings>`_,
            and include some of these placeholders: $auth_uri and $abort_uri.
        :param str success_template:
            The page will be displayed when authentication was largely successful.
            Placeholders can be any of these:
            https://tools.ietf.org/html/rfc6749#section-5.1
        :param str error_template:
            The page will be displayed when authentication encountered error.
            Placeholders can be any of these:
            https://tools.ietf.org/html/rfc6749#section-5.2
        :param callable auth_uri_callback:
            A function with the shape of lambda auth_uri: ...
            When a browser was unable to be launch, this function will be called,
            so that the app could tell user to manually visit the auth_uri.
        :param str browser_name:
            If you did
            ``webbrowser.register("xyz", None, BackgroundBrowser("/path/to/browser"))``
            beforehand, you can pass in the name "xyz" to use that browser.
            The default value ``None`` means using default browser,
            which is customizable by env var $BROWSER.
        :return:
            The auth response of the first leg of Auth Code flow,
            typically {"code": "...", "state": "..."} or {"error": "...", ...}
            See https://tools.ietf.org/html/rfc6749#section-4.1.2
            and https://openid.net/specs/openid-connect-core-1_0.html#AuthResponse
            Returns None when the state was mismatched, or when timeout occurred.
        """
        # Historically, the _get_auth_response() uses HTTPServer.handle_request(),
        # because its handle-and-retry logic is conceptually as easy as a while loop.
        # Also, handle_request() honors server.timeout setting, and CTRL+C simply works.
        # All those are true when running on Linux.
        #
        # However, the behaviors on Windows turns out to be different.
        # A socket server waiting for request would freeze the current thread.
        # Neither timeout nor CTRL+C would work. End user would have to do CTRL+BREAK.
        # https://stackoverflow.com/questions/1364173/stopping-python-using-ctrlc
        #
        # The solution would need to somehow put the http server into its own thread.
        # This could be done by the pattern of ``http.server.test()`` which internally
        # use ``ThreadingHTTPServer.serve_forever()`` (only available in Python 3.7).
        # Or create our own thread to wrap the HTTPServer.handle_request() inside.
        result = {}  # A mutable object to be filled with thread's return value
        t = threading.Thread(target=self._get_auth_response, args=(result,), kwargs=kwargs)
        t.daemon = True  # So that it won't prevent the main thread from exiting
        t.start()
        begin = time.time()
        while (time.time() - begin < timeout) if timeout else True:
            time.sleep(1)  # Short detection interval to make happy path responsive
            if not t.is_alive():  # Then the thread has finished its job and exited
                break
            while self._scheduled_actions and time.time() - begin > self._scheduled_actions[0][0]:
                _, callback = self._scheduled_actions.pop(0)
                callback()
        return result or None

    def _get_auth_response(
        self,
        result,
        auth_uri=None,
        timeout=None,
        state=None,
        welcome_template=None,
        success_template=None,
        error_template=None,
        auth_uri_callback=None,
        browser_name=None,
    ):
        welcome_uri = "http://localhost:{p}".format(p=self.get_port())
        abort_uri = "{loc}?error=abort".format(loc=welcome_uri)
        logger.debug("Abort by visit %s", abort_uri)
        self._server.welcome_page = Template(welcome_template or "").safe_substitute(
            auth_uri=auth_uri, abort_uri=abort_uri
        )
        if auth_uri:  # Now attempt to open a local browser to visit it
            _uri = welcome_uri if welcome_template else auth_uri
            logger.info("Open a browser on this device to visit: %s" % _uri)
            browser_opened = False
            try:
                browser_opened = _browse(_uri, browser_name=browser_name)
            except:  # noqa: E722
                # webbrowser.Error is purposely undefined outside of _browse().
                # Absorb and proceed. Because browser could be manually run elsewhere.
                logger.exception("_browse(...) unsuccessful")
            if not browser_opened:
                if not auth_uri_callback:
                    logger.warning(
                        "Found no browser in current environment. "
                        "If this program is being run inside a container "
                        "which either (1) has access to host network "
                        "(i.e. started by `docker run --net=host -it ...`), "
                        "or (2) published port {port} to host network "
                        "(i.e. started by `docker run -p 127.0.0.1:{port}:{port} -it ...`), "
                        "you can use browser on host to visit the following link. "
                        "Otherwise, this auth attempt would either timeout "
                        "(current timeout setting is {timeout}) "
                        "or be aborted by CTRL+C. Auth URI: {auth_uri}".format(
                            auth_uri=_uri, timeout=timeout, port=self.get_port()
                        )
                    )
                else:  # Then it is the auth_uri_callback()'s job to inform the user
                    auth_uri_callback(_uri)

        self._server.success_template = Template(
            success_template or "Authentication completed. You can close this window now."
        )
        self._server.error_template = Template(
            error_template or "Authentication failed. $error: $error_description. ($error_uri)"
        )

        self._server.timeout = timeout  # Otherwise its handle_timeout() won't work
        self._server.auth_response = {}  # Shared with _AuthCodeHandler
        self._server.auth_state = state  # So handler will check it before sending response
        while not self._closing:  # Otherwise, the handle_request() attempt
            # would yield noisy ValueError trace
            # Derived from
            # https://docs.python.org/2/library/basehttpserver.html#more-examples
            self._server.handle_request()
            if self._server.auth_response:
                break
        result.update(self._server.auth_response)  # Return via writable result param

    def close(self):
        """Either call this eventually; or use the entire class as context manager"""
        self._closing = True
        self._server.server_close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
