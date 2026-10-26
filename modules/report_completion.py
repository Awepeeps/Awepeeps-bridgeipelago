"""Independent progress and DeathLink completion checks for scheduled reports."""

from bs4 import BeautifulSoup


def deathlink_games_finished(html, slots):
    """Only explicitly configured DeathLink slots count; offline tags are unknown."""
    if slots is None:
        return False
    if not isinstance(slots, list) or any(not isinstance(slot, str) or not slot.strip() for slot in slots):
        raise ValueError("DailyPostConfig.DeathLinkSlots must be null or a list of exact slot names")
    if not slots:
        return True
    table = BeautifulSoup(html, "html.parser").find("table", id="checks-table")
    if table is None:
        raise ValueError("Tracker checks table is missing")
    statuses = {}
    for row in table.select("tbody tr"):
        cells = row.find_all("td")
        if len(cells) < 4:
            raise ValueError("Tracker status row is incomplete")
        name = cells[1].get_text(strip=True)
        if name in statuses:
            raise ValueError("Tracker slot names are ambiguous")
        statuses[name] = cells[3].get_text(strip=True)
    if any(slot not in statuses for slot in slots):
        raise ValueError("A configured DeathLink slot is missing from the tracker")
    return all(statuses[slot] == "Goal Completed" for slot in slots)


def all_locations_checked(html):
    table = BeautifulSoup(html, "html.parser").find("table", id="checks-table")
    if table is None:
        raise ValueError("Tracker checks table is missing")
    rows = table.select("tbody tr")
    if not rows:
        raise ValueError("Tracker checks table is empty")
    complete = True
    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 5:
            raise ValueError("Tracker checks row is incomplete")
        counts = cells[4].get_text(strip=True).split("/")
        if len(counts) != 2:
            raise ValueError("Tracker location counts are invalid")
        checked, total = (int(value.replace(",", "")) for value in counts)
        if checked < 0 or total < 0 or checked > total:
            raise ValueError("Tracker location counts are out of range")
        complete = complete and checked == total
    return complete
