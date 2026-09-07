# -*- coding: utf-8 -*-
import time
import os



from dotenv import load_dotenv
from tracker_api.pyLMUSharedMemory import lmu_data as api
from supabase import create_client


load_dotenv()

supabase_url = os.getenv("supabase_url")
supabase_key = os.getenv("supabase_key")

if not supabase_url or not supabase_key:
    raise ValueError("Supabase URL or key is not set in the environment variables.")

supabase = create_client(supabase_url, supabase_key)



last_lap_times = []
best_average_five = None

current_lap_number = 0
is_current_lap_valid = True

def format_time_to_string(seconds):
    minutes = int(seconds // 60)
    # Zamieniamy na int, aby pozbyć się ułamków przed formatowaniem
    seconds_int = int(seconds % 60) 
    milliseconds = int(round((seconds % 1) * 1000))
    # Teraz zadziała poprawny format: "Minuty:Sekundy:Milisekundy"
    return f"{minutes}:{seconds_int:02d}:{milliseconds:03d}"

def save_player_data_to_cloud(driver, track, car_class, car, best_average_five):
    formatted_time = format_time_to_string(best_average_five)
    
    try:
        response = supabase.table("leaderboard").upsert({
            "name": driver,
            "track": track,
            "class": car_class,
            "car": car,
            "avg_five": formatted_time
        }).execute()
        
        if response.status_code in [200, 201]:
            print(f"Zapisano dane do Supabase: {response.data}")
        else:
            print(f"Błąd podczas zapisywania danych do Supabase: {response.status_code}, {response.data}")
    except Exception as e:
        print(f"Wystąpił błąd podczas zapisywania danych do Supabase: {e}")

    finally:
        pass

def track_game_data():
    global current_lap_number, is_current_lap_valid, best_average_five
    
    print("Oczekiwanie na uruchomienie Le Mans Ultimate...")
    
    try:
        sim_info = api.SimInfo() # Poprawione wywołanie klasy z pliku lmu_data.py
        print("Połączono z telemetrią LMU!")
    except FileNotFoundError:
        print("Nie wykryto włączonej gry. Uruchom skrypt, gdy gra będzie działać.")
        return

    while True:
        try:
            telemetry = sim_info.LMUData.telemetry
            scoring = sim_info.LMUData.scoring
            
            player_idx = telemetry.playerVehicleIdx
            
            if not telemetry.playerHasVehicle:
                time.sleep(1)
                continue

            player_telem = telemetry.telemInfo[player_idx]
            player_scoring = scoring.vehScoringInfo[player_idx]

            if player_telem.mLapInvalidated or player_scoring.mInPits:
                is_current_lap_valid = False

            laps_completed = player_scoring.mTotalLaps
            
            if laps_completed > current_lap_number:
                last_lap_time = player_scoring.mLastLapTime
                
                if current_lap_number > 0 and last_lap_time > 0:
                    if is_current_lap_valid:
                        print(f"Czyste okrążenie: {last_lap_time:.3f} s")
                        driver_name = player_scoring.mDriverName.decode('windows-1252', errors='ignore').split('\x00')[0]
                        car_name = player_scoring.mVehicleName.decode('windows-1252', errors='ignore').split('\x00')[0]
                        class_name = player_scoring.mVehicleClass.decode('windows-1252', errors='ignore').split('\x00')[0]
                        track_name = scoring.scoringInfo.mTrackName.decode('windows-1252', errors='ignore').split('\x00')[0]
                        
                        # 3. Uporządkowana kolejność wysyłania argumentów
                        process_new_lap(last_lap_time, driver_name, track_name, class_name, car_name)
                    else:
                        print(f"Okrążenie nieważne: {last_lap_time:.3f} s. Reset serii.")
                        last_lap_times.clear()
                
                current_lap_number = laps_completed
                is_current_lap_valid = True
                
        except Exception:
            pass
            
        time.sleep(0.05)

# Odbiera parametry w tej samej kolejności, w której zostały wysłane
def process_new_lap(lap_time, driver_name, track_name, class_name, car_name):
    global best_average_five
    
    last_lap_times.append(lap_time)
    
    if len(last_lap_times) > 5:
        last_lap_times.pop(0)

    if len(last_lap_times) == 5:
        average_five = sum(last_lap_times) / 5.0
        print(f"Średnia z 5 ostatnich kółek: {average_five:.3f} s")
        
        if best_average_five is None or average_five < best_average_five:
            best_average_five = average_five
            print(f"NOWY REKORD ŚREDNIEJ: {best_average_five:.3f} s!")
            save_player_data_to_cloud(driver_name, track_name, class_name, car_name, best_average_five)

if __name__ == "__main__":
    track_game_data()