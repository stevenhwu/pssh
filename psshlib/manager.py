import select
import threading
import Queue

class Manager(object):
    """Executes tasks concurrently.

    Tasks are added with add_task() and executed in parallel with run().

    Arguments:
        limit: Maximum number of commands running at once.
        timeout: Maximum allowed execution time in seconds.
    """
    def __init__(self, limit, timeout):
        self.limit = limit
        self.timeout = timeout
        self.iomap = IOMap()

        self.tasks = []
        self.running = []
        self.done = []

    def run(self):
        """Processes tasks previously added with add_task."""
        for task in self.tasks:
            if task.outdir or task.errdir:
                writer = Writer()
                writer.start()
                break
        else:
            writer = None

        try:
            self.start_tasks(writer)
            wait = None
            while self.running or self.tasks:
                if wait == None or wait < 1:
                    wait = 1
                self.iomap.poll(wait)
                self.check_tasks()
                wait = self.check_timeout()
        except KeyboardInterrupt:
            self.interrupted()

        if writer:
            writer.queue.put((Writer.ABORT, None))
            writer.join()

    def add_task(self, task):
        """Adds a Task to be processed with run()."""
        self.tasks.append(task)

    def start_tasks(self, writer):
        """Starts as many tasks as allowed."""
        while 0 < len(self.tasks) and len(self.running) < self.limit:
            task = self.tasks.pop(0)
            self.running.append(task)
            task.start(self.iomap, writer)

    def check_tasks(self):
        """Checks to see if any tasks have terminated."""
        still_running = []
        for task in self.running:
            if task.running():
                still_running.append(task)
            else:
                self.finished(task)
        self.running = still_running

    def check_timeout(self):
        """Kills timed-out processes and returns the lowest time left."""
        if self.timeout <= 0:
            return None

        min_timeleft = None
        for task in self.running:
            timeleft = self.timeout - task.elapsed()
            if timeleft <= 0:
                task.timedout()
                continue
            if min_timeleft is None or timeleft < min_timeleft:
                min_timeleft = timeleft

        return max(0, min_timeleft)

    def interrupted(self):
        """Cleans up after a keyboard interrupt."""
        for task in self.running:
            task.interrupted()
            self.finished(task)

        for task in self.tasks:
            task.cancel()
            self.finished(task)

    def finished(self, task):
        """Marks a task as complete and reports its status to stdout."""
        self.done.append(task)
        n = len(self.done)
        task.report(n)


class IOMap(object):
    """A manager for file descriptors and their associated handlers.

    The poll method dispatches events to the appropriate handlers.
    """
    def __init__(self):
        self.map = {}
        self.poller = select.poll()

    def register(self, fd, handler, read=False, write=False):
        """Registers an IO handler for a file descriptor.
        
        Either read or write (or both) must be specified.
        """
        self.map[fd] = handler

        eventmask = 0
        if read:
            eventmask |= select.POLLIN
        if write:
            eventmask |= select.POLLOUT
        if not eventmask:
            raise ValueError("Register must be called with read or write.")
        self.poller.register(fd, eventmask)

    def unregister(self, fd):
        """Unregisters the given file descriptor."""
        self.poller.unregister(fd)
        del self.map[fd]

    def poll(self, timeout=None):
        """Performs a poll and dispatches the resulting events."""
        for fd, event in self.poller.poll(timeout):
            handler = self.map[fd]
            handler(fd, event, self)


class Writer(threading.Thread):
    """Thread that writes to files by processing requests from a Queue.

    Until AIO becomes widely available, it is impossible to make a nonblocking
    write to an ordinary file.  The Writer thread processes all writing to
    ordinary files so that the main thread can work without blocking.
    """
    EOF = object()
    ABORT = object()

    def __init__(self):
        threading.Thread.__init__(self)
        # A daemon thread automatically dies if the program is terminated.
        self.setDaemon(True)
        self.queue = Queue.Queue()

    def run(self):
        while True:
            file, data = self.queue.get()
            if file == self.ABORT:
                return
            if data == self.EOF:
                file.close()
            else:
                print >>file, data,

    def write(self, fd, data):
        """Called from another thread to enqueue a write."""
        self.queue.put((fd, data))
