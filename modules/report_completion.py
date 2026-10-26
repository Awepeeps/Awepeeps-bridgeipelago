"""Determine whether every world's locations have been checked or released."""

from bs4 import BeautifulSoup


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
