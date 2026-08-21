from .discovery import DiscoveryController
from .download import DownloadController
from .supply_scan import SupplyScanController


class Runtime:
    def __init__(self):
        self.discovery = DiscoveryController()
        self.download = DownloadController()
        self.supply_scan = SupplyScanController()

    def snapshot(self):
        return {
            "discovery": self.discovery.snapshot(),
            "downloads": self.download.snapshot(),
            "discovery_status": self.discovery.snapshot()["status"],
            "supply_scan": self.supply_scan.snapshot(),
        }

    def start_discovery(self, language, target, provider="youtube"):
        return self.discovery.start(language, target, provider=provider)

    def restore_discovery(self, language):
        return self.discovery.restore_latest(language)

    def pause_discovery(self):
        return self.discovery.pause()

    def resume_discovery(self):
        return self.discovery.resume()

    def stop_discovery(self):
        return self.discovery.stop()

    def start_supply_scan(self, language, provider, max_candidates):
        return self.supply_scan.start(language, provider, max_candidates)

    def stop_supply_scan(self):
        return self.supply_scan.stop()


runtime = Runtime()
