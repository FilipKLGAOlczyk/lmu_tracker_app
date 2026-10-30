# -*- coding: utf-8 -*-
import os
import time

import psutil
from dotenv import load_dotenv
from supabase import create_client

from pyLMUSharedMemory import lmu_data as api

load_dotenv()

SUPABASE_URL = os.getenv("supabase_url")
SUPABASE_KEY = os.getenv("supabase_key")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("Supabase URL or key is not set in the environment variables.")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

GAME_PROCESSES = {"le mans ultimate.exe", "lemansultimate.exe"}
LAPS_IN_SERIES = 5       # ile czystych okrążeń z rzędu tworzy serię
POLL_INTERVAL = 0.05     # co ile sekund odczytujemy telemetrię

# Najlepsza średnia wysłana w tym uruchomieniu, osobno dla każdej kombinacji
# (kierowca, tor, klasa, auto). Ostateczną decyzję o rekordzie podejmuje baza.
local_best = {}


class SessionState:
    """Stan śledzenia bieżącej sesji."""

    def __init__(self):
        self.combo = None         # (kierowca, tor, klasa, auto)
        self.lap_number = None    # ile okrążeń ukończono przy ostatnim sprawdzeniu
        self.lap_valid = True     # czy bieżące okrążenie jest dotąd czyste
        self.laps = []            # czasy ostatnich czystych okrążeń z rzędu

    def restart(self, combo, lap_number):
        self.combo = combo
        self.lap_number = lap_number
        self.lap_valid = True
        self.laps.clear()


def game_running():
    return any(
        (p.info["name"] or "").lower() in GAME_PROCESSES
        for p in psutil.process_iter(["name"])
    )


def decode(raw):
    return raw.decode("windows-1252", errors="ignore").split("\x00")[0].strip()


def format_time(seconds):
    """Sekundy -> 'm:ss:mmm' (liczone na milisekundach, bez błędów zaokrąglania)."""
    total_ms = int(round(seconds * 1000))
    minutes, rest = divmod(total_ms, 60_000)
    secs, ms = divmod(rest, 1000)
    return f"{minutes}:{secs:02d}:{ms:03d}"


def submit_time(combo, average_seconds):
    """Wysyła średnią do bazy. Zwraca True/False (czy to nowy rekord w bazie)
    albo None, gdy wystąpił błąd komunikacji."""
    driver, track, car_class, car = combo
    try:
        response = supabase.rpc("submit_time", {
            "p_name": driver,
            "p_track": track,
            "p_class": car_class,
            "p_car": car,
            "p_avg_s": round(average_seconds, 3),
            "p_avg_text": format_time(average_seconds),
        }).execute()
        return bool(response.data)
    except Exception as e:
        print(f"Wystąpił błąd komunikacji z Supabase: {e}")
        return None


def process_clean_lap(state, lap_time):
    state.laps.append(lap_time)
    if len(state.laps) > LAPS_IN_SERIES:
        state.laps.pop(0)

    if len(state.laps) < LAPS_IN_SERIES:
        print(f"Czyste okrążenia w serii: {len(state.laps)}/{LAPS_IN_SERIES}")
        return

    average = sum(state.laps) / LAPS_IN_SERIES
    print(f"Średnia z {LAPS_IN_SERIES} ostatnich kółek: {average:.3f} s")

    if average >= local_best.get(state.combo, float("inf")):
        return

    result = submit_time(state.combo, average)
    if result is None:
        return  # błąd sieci: spróbujemy ponownie po następnym okrążeniu
    local_best[state.combo] = average
    if result:
        print(f"NOWY REKORD ŚREDNIEJ w bazie: {format_time(average)}")
    else:
        print("W bazie jest już lepszy wynik dla tej kombinacji.")


def read_combo(scoring, player_scoring):
    return (
        decode(player_scoring.mDriverName),
        decode(scoring.scoringInfo.mTrackName),
        decode(player_scoring.mVehicleClass),
        decode(player_scoring.mVehicleName),
    )


def tick(sim_info, state):
    telemetry = sim_info.LMUData.telemetry
    scoring = sim_info.LMUData.scoring

    if not telemetry.playerHasVehicle:
        return

    idx = telemetry.playerVehicleIdx
    player_telem = telemetry.telemInfo[idx]
    player_scoring = scoring.vehScoringInfo[idx]

    combo = read_combo(scoring, player_scoring)
    laps_completed = player_scoring.mTotalLaps

    # Pierwsze odczyty, zmiana toru/klasy/auta albo nowa sesja (licznik okrążeń
    # spadł) -> zaczynamy serię od zera, żeby nie mieszać różnych aut i torów.
    if state.combo != combo or state.lap_number is None or laps_completed < state.lap_number:
        state.restart(combo, laps_completed)
        return

    if player_telem.mLapInvalidated or player_scoring.mInPits:
        state.lap_valid = False

    if laps_completed > state.lap_number:
        last_lap_time = player_scoring.mLastLapTime
        if state.lap_number > 0 and last_lap_time > 0:
            if state.lap_valid:
                print(f"Czyste okrążenie: {last_lap_time:.3f} s")
                process_clean_lap(state, last_lap_time)
            else:
                print(f"Okrążenie nieważne: {last_lap_time:.3f} s. Reset serii.")
                state.laps.clear()
        state.lap_number = laps_completed
        state.lap_valid = True


def connect_to_telemetry():
    """Łączy z pamięcią współdzieloną; gra może jeszcze się ładować, więc ponawia."""
    while game_running():
        try:
            sim_info = api.SimInfo()
            print("Połączono z telemetrią LMU!")
            return sim_info
        except Exception as e:
            print(f"Czekam na telemetrię ({e})...")
            time.sleep(3)
    return None


def track_game_data():
    print("Oczekiwanie na uruchomienie Le Mans Ultimate...")
    while True:
        while not game_running():
            time.sleep(2)

        sim_info = connect_to_telemetry()
        if sim_info is None:
            continue

        state = SessionState()
        last_game_check = time.monotonic()
        last_error = None

        while True:
            if time.monotonic() - last_game_check > 5:
                last_game_check = time.monotonic()
                if not game_running():
                    print("Gra została zamknięta. Oczekiwanie na ponowne uruchomienie...")
                    break

            try:
                tick(sim_info, state)
                last_error = None
            except Exception as e:
                message = f"{type(e).__name__}: {e}"
                if message != last_error:  # ten sam błąd wypisujemy tylko raz
                    print(f"Błąd odczytu telemetrii: {message}")
                    last_error = message
                time.sleep(1)
                continue

            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        track_game_data()
    except KeyboardInterrupt:
        print("Zakończono.")