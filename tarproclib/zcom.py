import sys
import time
from builtins import object
from urllib.parse import urlparse
import braceexpand

import msgpack
import zmq
import logging


schemes = dict(
    # (KIND, BIND)
    zpush=(zmq.PUSH, False),
    zpull=(zmq.PULL, True),
    zpub=(zmq.PUB, True),
    zsub=(zmq.SUB, False),
    zrpush=(zmq.PUSH, True),
    zrpull=(zmq.PULL, False),
    zrpub=(zmq.PUB, False),
    zrsub=(zmq.SUB, True)
)

def zmq_make(context, url, linger=0):
    addr = urlparse(url)
    scheme, transport = (addr.scheme.split("+", 2)+["tcp"])[:2]
    kind, bind = schemes[scheme]
    logging.info("kind %s bind %s", kind, bind)
    socket = context.socket(kind)
    socket.setsockopt(zmq.LINGER, linger)
    return socket


def zmq_connect(socket, urls, topic=""):
    """Explicitly connect to a ZMQ socket.

    :param url: ZMQ-URL to connect to  (Default value = "")
    :param topic: topic to subscribe to for SUB sockets (Default value = "")

    """
    assert isinstance(urls, list)
    for url in urls:
        assert len(url) > 1
        addr = urlparse(url)
        scheme, transport = (addr.scheme.split("+", 2)+["tcp"])[:2]
        print(url, addr, scheme, transport)
        kind, bind = schemes[scheme]
        logging.info("kind %s bind %s", kind, bind)
        try:
            location = transport+"://"+addr.netloc
            if transport == "ipc":
                location += addr.path
            if bind:
                logging.info("binding to %s", location)
                socket.bind(location)
            else:
                logging.info("connecting to %s", location)
                socket.connect(location)
            if kind == zmq.SUB:
                logging.info("subscribing to '%s'", topic)
                socket.setsockopt_string(zmq.SUBSCRIBE, topic)
            return socket
        except Exception as e:
            print("error: url {} location {} kind {}".format(
                url, location, kind))
            raise e

def urls2list(urls, noexpand=False):
    if isinstance(urls, str):
        urls = [urls]
    if not noexpand:
        temp = []
        for u in urls:
            temp += list(braceexpand.braceexpand(u))
        urls = temp
    return urls

class Connection(object):
    """A class for sending/receiving samples via ZMQ sockets."""
    def __init__(self, urls=None, noexpand=False, keep_meta=True, **kw):
        """Initialize a connection.

        :param urls:  list of ZMQ-URL to connect to (Default value = None)
        :param noexpand: do not expand braces in URLs (Default value = False)

        """
        self.context = zmq.Context()
        self.socket = None
        if urls is not None:
            urls = urls2list(urls, noexpand=noexpand)
            self.socket = zmq_make(self.context, urls[0])
            zmq_connect(self.socket, urls)

    def connect(self, urls, topic="", noexpand=False):
        urls = urls2list(urls, noexpand=noexpand)
        self.socket = zmq_make(self.context, urls[0])
        for url in urls:
            zmq_connect(self.socket, url)

    def close(self, linger=-1):
        """Close the connection."""
        self.socket.close(linger=linger)

    def send(self, sample):
        """Send data over the connection.

        :param sample: sample to be sent

        """
        assert isinstance(sample, dict)
        data = msgpack.packb(sample)
        self.socket.send(data)

    def send_eof(self):
        data = msgpack.packb(dict(__EOF__=True))
        self.socket.send(data)
        time.sleep(1.0)

    def write(self, sample):
        self.send(sample)

    def recv(self):
        """Receive data from the connection."""
        data = self.socket.recv()
        sample = msgpack.unpackb(data)
        assert isinstance(sample, dict)
        data = {k.decode("utf-8") if isinstance(k, bytes) else k: v for k, v in sample.items()}
        return data

    def serve(self, source, report=-1):
        """Serve data from an iterator.

        :param source: iterator yielding lists/tuples of tensors
        :param report: how often to report statistics (Default value = -1)

        """
        count = 0
        next_report = 0
        for sample in source:
            self.send(sample)
            if report > 0 and count >= next_report:
                print("count", count, self.stats.summary())
                next_report += report
            count += self.batchsize(sample)

    def __iter__(self, report=-1):
        """Receive data through an iterator"""
        count = 0
        next_report = 0
        while True:
            result = self.recv()
            #print("***", result.keys(), file=sys.stderr)
            if result.get("__EOF__", False):
                break
            if report > 0 and count >= next_report:
                print("count", count, self.stats.summary())
                next_report += report
            yield result

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()