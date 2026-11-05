from datetime import datetime


class HackathonDiscoveryAgent:
    opportunity_type = "hackathon"
    source_domains = ("unstop.com", "devfolio.co", "hackerearth.com", "devpost.com")

    def search_tasks(self, locations: list[str]) -> list[tuple[str, str, list[str] | None]]:
        year = datetime.now().year
        tasks = [(location, f"hackathon {location} {year} application registration opportunity", list(self.source_domains)) for location in (locations or ["India", "Online"])]
        tasks.append(("India and online", f"hackathon {year} India university college official registration", None))
        return tasks
