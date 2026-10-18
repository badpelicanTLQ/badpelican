"""Find nearby open badminton tournaments on BadNet and notify a WhatsApp group."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag
from geopy.distance import geodesic
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderServiceError


PELISSANNE_COORDINATES = (43.6314, 5.1514)
MAX_DISTANCE_KM = 100
BADNET_BASE_URL = "https://badnet.fr"
# Nearby departments, including neighboring areas whose boundary towns can be
# within 100 km. The geodesic check below is the final distance filter.
SEARCH_DEPARTMENTS = ("92", "94", "78", "93", "2423", "79", "90", "108")
STATE_PATH = Path(__file__).with_name("sent_tournaments.json")
REQUEST_TIMEOUT_SECONDS = 20

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Tournament:
    name: str
    city: str
    event_date: str
    url: str
    coordinates: tuple[float, float] | None = None


def _route_data(element: Tag) -> dict[str, Any]:
    try:
        route = json.loads(str(element.get("data-ic_url", "{}")))
    except json.JSONDecodeError:
        return {}
    return route if isinstance(route, dict) else {}


def _action_hash(element: Tag) -> str:
    return str(element.get("data-ic_a", "")).strip()


def _badnet_search_action(session: requests.Session) -> str:
    """Follow BadNet's public AJAX navigation and discover the current search action."""
    try:
        home = session.get(BADNET_BASE_URL, timeout=REQUEST_TIMEOUT_SECONDS)
        home.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"Impossible d'ouvrir BadNet : {exc}") from exc

    home_soup = BeautifulSoup(home.text, "html.parser")
    home_link = next(
        (
            element
            for element in home_soup.select("[data-ic_url][data-ic_a]")
            if _route_data(element).get("url") == "accueil"
        ),
        None,
    )
    if home_link is None:
        raise RuntimeError("Le lien d'accueil public de BadNet est introuvable.")

    try:
        landing = session.post(
            urljoin(BADNET_BASE_URL, "/index.php"),
            data={
                "ic_a": _action_hash(home_link),
                "ic_ajax": "1",
                "ic_t": "main",
                "ic_url": json.dumps({"url": "accueil"}),
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        landing.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"Impossible de charger l'accueil BadNet : {exc}") from exc

    landing_soup = BeautifulSoup(landing.text, "html.parser")
    tournament_tab = next(
        (
            element
            for element in landing_soup.select("[data-ic_url][data-ic_a]")
            if _route_data(element).get("url") == "accueil/indiv"
        ),
        None,
    )
    if tournament_tab is None:
        raise RuntimeError("L'onglet public des tournois BadNet est introuvable.")

    try:
        search_page = session.post(
            urljoin(BADNET_BASE_URL, "/index.php"),
            data={
                "ic_a": _action_hash(tournament_tab),
                "ic_ajax": "1",
                "ic_t": "search-content",
                "ic_url": json.dumps({"url": "accueil/indiv"}),
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        search_page.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"Impossible d'ouvrir la recherche BadNet : {exc}") from exc

    form = BeautifulSoup(search_page.text, "html.parser").select_one("#search_form")
    if form is None or not _action_hash(form):
        raise RuntimeError("Le formulaire de recherche des tournois BadNet est introuvable.")
    return _action_hash(form)


def _request_tournament_page(
    session: requests.Session,
    action: str,
    department: str,
    page: int,
) -> BeautifulSoup:
    payload = {
        "ic_a": action,
        "ic_ajax": "1",
        "type_event": "70",
        "departement": department,
        "ligue": "-1",
        "rayon": str(MAX_DISTANCE_KM),
        "open": "1",
        "page": str(page),
    }
    try:
        response = session.post(
            urljoin(BADNET_BASE_URL, "/index.php"),
            data=payload,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(
            f"Impossible de récupérer les tournois BadNet du département {department} : {exc}"
        ) from exc
    soup = BeautifulSoup(response.text, "html.parser")
    if soup.select_one("[data-ic_js='searchResult']") is None:
        raise RuntimeError(
            "La réponse BadNet ne contient pas les résultats attendus; "
            "son interface a peut-être changé."
        )
    return soup


def _parse_badnet_markers(soup: BeautifulSoup) -> list[Tournament]:
    markers = soup.select_one(".b-markers[data-markers]")
    if markers is None:
        if soup.select_one("[data-ic_js='searchResult']") is not None:
            return []
        raise RuntimeError("Les résultats de tournois BadNet sont introuvables.")
    try:
        records = json.loads(str(markers.get("data-markers", "[]")))
    except json.JSONDecodeError as exc:
        raise RuntimeError("Les données de tournois renvoyées par BadNet sont invalides.") from exc
    if not isinstance(records, list):
        raise RuntimeError("Le format des résultats de tournois BadNet est inattendu.")

    tournaments = []
    for record in records:
        if not isinstance(record, dict):
            continue
        name = str(record.get("title") or record.get("name") or "").strip()
        city = str(record.get("place") or "").strip()
        event_date = str(record.get("date") or "").strip()
        url = urljoin(BADNET_BASE_URL, str(record.get("url") or ""))
        if (
            not name
            or not city
            or not event_date
            or "interclub" in name.casefold()
            or not url.startswith(f"{BADNET_BASE_URL}/")
        ):
            continue

        latitude, longitude = record.get("lat"), record.get("lng")
        coordinates = None
        if latitude is not None and longitude is not None:
            try:
                coordinates = (float(latitude), float(longitude))
            except (TypeError, ValueError):
                LOGGER.warning("Coordonnées invalides pour le tournoi %s.", name)
        tournaments.append(
            Tournament(
                name=name,
                city=city,
                event_date=event_date,
                url=url,
                coordinates=coordinates,
            )
        )
    return tournaments


def get_open_tournaments() -> list[Tournament]:
    """Fetch BadNet's public open-tournament search results, including all pages."""
    session = requests.Session()
    session.headers.update(
        {"User-Agent": "BadPelican tournament availability checker"}
    )
    action = _badnet_search_action(session)
    unique: dict[str, Tournament] = {}

    for department in SEARCH_DEPARTMENTS:
        first_page = _request_tournament_page(session, action, department, 0)
        for tournament in _parse_badnet_markers(first_page):
            unique[tournament.url] = tournament

        page_numbers = [
            int(link["data-page"])
            for link in first_page.select(".pager [data-page]")
            if str(link.get("data-page", "")).isdigit()
        ]
        for page in range(1, max(page_numbers, default=0) + 1):
            soup = _request_tournament_page(session, action, department, page)
            for tournament in _parse_badnet_markers(soup):
                unique[tournament.url] = tournament

    return list(unique.values())


def _load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"sent": [], "geocode_cache": {}}
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Impossible de lire l'état {STATE_PATH}: {exc}") from exc
    if not isinstance(state, dict):
        raise RuntimeError(f"Le fichier d'état {STATE_PATH} doit contenir un objet JSON.")
    state.setdefault("sent", [])
    state.setdefault("geocode_cache", {})
    return state


def _save_state(state: dict[str, Any]) -> None:
    """Atomically persist IDs after successful sends and cached city coordinates."""
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=STATE_PATH.parent,
            delete=False,
        ) as temporary_file:
            json.dump(state, temporary_file, ensure_ascii=False, indent=2, sort_keys=True)
            temporary_file.write("\n")
            temporary_path = temporary_file.name
        os.replace(temporary_path, STATE_PATH)
    except OSError as exc:
        if temporary_path and os.path.exists(temporary_path):
            os.unlink(temporary_path)
        raise RuntimeError(f"Impossible d'enregistrer l'état des notifications : {exc}") from exc


def _coordinates_for_city(city: str, cache: dict[str, Any]) -> tuple[float, float] | None:
    key = city.casefold().strip()
    if key in cache:
        coordinates = cache[key]
        return (float(coordinates[0]), float(coordinates[1])) if coordinates else None

    try:
        geolocator = Nominatim(user_agent="badpelican-badnet-tournament-checker")
        location = geolocator.geocode(f"{city}, France", timeout=10)
    except GeocoderServiceError as exc:
        LOGGER.warning("Géocodage impossible pour %s : %s", city, exc)
        return None
    finally:
        time.sleep(1)
    if not location:
        LOGGER.warning("Ville introuvable pour le géocodage : %s", city)
        return None

    coordinates = (location.latitude, location.longitude)
    cache[key] = coordinates
    return coordinates


def send_whatsapp_message(message: str) -> None:
    """Send a message using the Green-API WhatsApp endpoint."""
    instance_id = os.getenv("WHATSAPP_INSTANCE_ID")
    token = os.getenv("WHATSAPP_TOKEN")
    chat_id = os.getenv("WHATSAPP_CHAT_ID")
    if not all((instance_id, token, chat_id)):
        raise RuntimeError(
            "Les variables WHATSAPP_INSTANCE_ID, WHATSAPP_TOKEN et "
            "WHATSAPP_CHAT_ID doivent être configurées."
        )

    endpoint = f"https://api.green-api.com/waInstance{instance_id}/sendMessage/{token}"
    try:
        response = requests.post(
            endpoint,
            json={"chatId": chat_id, "message": message},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        result = response.json()
    except requests.RequestException as exc:
        raise RuntimeError(f"Échec de l'envoi WhatsApp : {exc}") from exc
    except ValueError as exc:
        raise RuntimeError("Réponse JSON invalide de l'API WhatsApp.") from exc

    if not isinstance(result, dict) or result.get("error") or not result.get("idMessage"):
        raise RuntimeError(f"Erreur de l'API WhatsApp : {result}")


def main() -> None:
    state = _load_state()
    sent = set(state["sent"])
    geocode_cache = state["geocode_cache"]
    tournaments = get_open_tournaments()
    LOGGER.info("%d tournoi(s) trouvé(s) sur BadNet.", len(tournaments))

    for tournament in tournaments:
        if tournament.url in sent:
            continue

        coordinates = tournament.coordinates or _coordinates_for_city(
            tournament.city, geocode_cache
        )
        if coordinates is None:
            _save_state(state)
            continue

        distance_km = geodesic(PELISSANNE_COORDINATES, coordinates).kilometers
        if distance_km > MAX_DISTANCE_KM:
            continue

        message = (
            "🏸 Nouveau tournoi de badminton ouvert près de Pélissanne !\n"
            f"Nom : {tournament.name}\n"
            f"Ville : {tournament.city}\n"
            f"Date : {tournament.event_date}\n"
            f"Lien BadNet : {tournament.url}\n"
            f"Distance : {distance_km:.1f} km"
        )
        send_whatsapp_message(message)
        sent.add(tournament.url)
        state["sent"] = sorted(sent)
        _save_state(state)
        LOGGER.info("Notification envoyée : %s (%s).", tournament.name, tournament.city)

    _save_state(state)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, requests.RequestException) as exc:
        LOGGER.error("%s", exc)
        raise SystemExit(1) from exc
