from datetime import datetime


class InternshipDiscoveryAgent:
    opportunity_type = "internship"
    source_domains = ("wellfound.com", "internshala.com", "linkedin.com", "greenhouse.io", "lever.co")

    def search_tasks(self, locations: list[str]) -> list[tuple[str, str, list[str] | None]]:
        year = datetime.now().year
        tasks = [(location, f"internship {location} {year} application opportunity entry level", list(self.source_domains)) for location in (locations or ["India", "Online"])]
        tasks.append(("India and remote", f"internship {year} India company careers official application", None))
        return tasks
