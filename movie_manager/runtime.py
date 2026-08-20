from .discovery import DiscoveryController
from .download import DownloadController


class Runtime:
    def __init__(self):
        self.discovery = DiscoveryController()
        self.download = DownloadController()

    def snapshot(self):
        return {
            "discovery": self.discovery.snapshot(),
            "downloads": self.download.snapshot(),
            "discovery_status": self.discovery.snapshot()["status"],
        }

    def start_discovery(self, language, target):
        return self.discovery.start(language, target)

    def restore_discovery(self, language):
        return self.discovery.restore_latest(language)

    def pause_discovery(self):
        return self.discovery.pause()

    def resume_discovery(self):
        return self.discovery.resume()

    def stop_discovery(self):
        return self.discovery.stop()


runtime = Runtime()
