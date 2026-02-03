import flet as ft
import sqlite3
import threading
import speech_recognition as sr
import google.generativeai as genai
import os
import webbrowser
from dotenv import load_dotenv
import time
import pyttsx3 
import subprocess 
import datetime   

# IMPORT YOUR MUSIC LIBRARY
try:
    from musicLibrary import music
except ImportError:
    music = {} 
    print("WARNING: musicLibrary.py not found.")

# --- CONFIGURATION ---
load_dotenv()
API_KEY = os.getenv("GEMINI_API_KEY")

if API_KEY:
    genai.configure(api_key=API_KEY)
else:
    print("WARNING: GEMINI_API_KEY not found in .env file.")

# --- VOICE ENGINE SETUP ---
def speak_text(text):
    """
    Safely speaks text with a HARD delay afterwards.
    Tuned for MAXIMUM VOLUME and CLEAR PRONUNCIATION.
    """
    try:
        print(f"DEBUG: Speaking -> {text}")
        local_engine = pyttsx3.init()
        
        voices = local_engine.getProperty('voices')
        if len(voices) > 1:
            local_engine.setProperty('voice', voices[1].id)
        
        local_engine.setProperty('rate', 140)
        local_engine.setProperty('volume', 1.0)
        
        local_engine.say(text)
        local_engine.runAndWait() 
        local_engine.stop()       
        del local_engine          
        
        time.sleep(1.0) 
        
    except Exception as e:
        print(f"TTS Error: {e}")

# --- DATABASE MANAGER ---
class Database:
    def __init__(self):
        self.conn = sqlite3.connect("user_data.db", check_same_thread=False)
        self.create_table()

    def create_table(self):
        cursor = self.conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE,
                password TEXT,
                first_name TEXT,
                last_name TEXT,
                age INTEGER,
                occupation_type TEXT,
                institution_or_company TEXT,
                wake_word TEXT DEFAULT 'hey alexa'
            )
        """)
        self.conn.commit()

    def register_user(self, email, password, fname, lname, age, occ_type, place):
        cursor = self.conn.cursor()
        try:
            cursor.execute("""
                INSERT INTO users (email, password, first_name, last_name, age, occupation_type, institution_or_company)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (email, password, fname, lname, age, occ_type, place))
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def login_user(self, email, password):
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM users WHERE email = ? AND password = ?", (email, password))
        return cursor.fetchone()

    def update_wake_word(self, user_id, new_word):
        cursor = self.conn.cursor()
        cursor.execute("UPDATE users SET wake_word = ? WHERE id = ?", (new_word, user_id))
        self.conn.commit()

db = Database()

# --- GLOBAL STATE ---
class AppState:
    current_user = None 
    is_listening = False
    wake_word = "hey alexa"

state = AppState()

# --- AI & VOICE LOGIC (THREADED) ---
def process_voice_command(command_text, page, status_control):
    command = command_text.lower()
    print(f"DEBUG: Processing command: {command}")
    
    # --- LAYER 1: WEBSITE SHORTCUTS (SMART MATCHING) ---
    sites = {
        "youtube": "https://www.youtube.com",
        "google": "https://www.google.com",
        "facebook": "https://www.facebook.com",
        "instagram": "https://www.instagram.com",
        "linkedin": "https://www.linkedin.com"
    }
    
    for site_key, url in sites.items():
        if site_key in command:
            # LOGIC: Open if command contains "open" OR if the command is JUST the site name (mic cutoff)
            if "open" in command or "launch" in command or command.strip() == site_key:
                site_name = site_key.title()
                update_status(page, status_control, f"Opening {site_name}...", is_active=True)
                speak_text(f"Opening {site_name}")
                webbrowser.open(url)
                update_status(page, status_control, "Idle - Assistant On")
                return

    # --- LAYER 1.5: TIME & DATE ---
    if "time" in command and "what" in command:
        current_time = datetime.datetime.now().strftime("%I:%M %p") 
        update_status(page, status_control, f"Time: {current_time}", is_active=True)
        speak_text(f"The time is {current_time}")
        update_status(page, status_control, "Idle - Assistant On")
        return

    if "date" in command and "what" in command:
        current_date = datetime.datetime.now().strftime("%A, %B %d, %Y")
        update_status(page, status_control, f"Date: {current_date}", is_active=True)
        speak_text(f"Today is {current_date}")
        update_status(page, status_control, "Idle - Assistant On")
        return

    # --- LAYER 2: SYSTEM APPS ---
    if "open calculator" in command:
        update_status(page, status_control, "Opening Calculator...", is_active=True)
        speak_text("Opening Calculator")
        subprocess.Popen('calc.exe')
        update_status(page, status_control, "Idle - Assistant On")
        return

    if "open notepad" in command:
        update_status(page, status_control, "Opening Notepad...", is_active=True)
        speak_text("Opening Notepad")
        subprocess.Popen('notepad.exe')
        update_status(page, status_control, "Idle - Assistant On")
        return

    # --- LAYER 3: MUSIC PLAYER (SMART LIBRARY PRIORITY) ---
    for song_key, song_url in music.items():
        if song_key in command:
            response_text = f"Playing {song_key} from Library..."
            update_status(page, status_control, response_text, is_active=True)
            speak_text(f"Playing {song_key}")
            webbrowser.open(song_url)
            time.sleep(3)
            update_status(page, status_control, "Idle - Assistant On")
            return

    if "play" in command:
        song = command.replace("play", "").strip()
        if song: 
            response_text = f"Playing {song} on YouTube..."
            update_status(page, status_control, response_text, is_active=True)
            speak_text(f"Playing {song}")
            webbrowser.open(f"https://www.youtube.com/results?search_query={song}")
            time.sleep(3) 
            update_status(page, status_control, "Idle - Assistant On")
            return

    # --- LAYER 4: AI INTELLIGENCE ---
    try:
        update_status(page, status_control, "Thinking...", is_active=True)
        
        if API_KEY:
            selected_model = "gemini-2.5-flash" 
            
            system_role = (
                "You are a helpful, friendly, and concise AI assistant. "
                "You are speaking to the user through voice. Keep your responses brief and conversational. "
                "Do NOT use asterisks, markdown formatting, or special characters in your response. "
                "Speak naturally as if having a voice conversation."
            )
            
            model = genai.GenerativeModel(selected_model, system_instruction=system_role)
            
            response = model.generate_content(command)
            ai_reply = response.text
            
            clean_reply = ai_reply.replace("*", "").replace("#", "")
        else:
            ai_reply = "API Key missing."
            clean_reply = "I cannot find my API key."
        
        update_status(page, status_control, clean_reply, is_active=True)
        speak_text(clean_reply)
    
    except Exception as e:
        print(f"AI Error: {e}")
        error_msg = "I'm having trouble connecting to the server."
        update_status(page, status_control, error_msg)
        speak_text(error_msg)

def update_status(page, control, text, is_active=False):
    control.value = text
    control.color = "cyanAccent" if is_active else "grey400"
    page.update()

# --- REVISED LISTENER FUNCTION ---
def start_background_listener(page, status_control):
    recognizer = sr.Recognizer()
    recognizer.dynamic_energy_threshold = False 
    recognizer.energy_threshold = 400 
    
    # Initialize Mic Once
    try:
        with sr.Microphone() as source:
            print("DEBUG: Adjusting for background noise...")
            recognizer.adjust_for_ambient_noise(source, duration=1)
    except OSError:
        print("CRITICAL ERROR: No Microphone found! Check Windows Settings.")
        return

    while True:
        if not state.is_listening:
            time.sleep(1)
            continue
            
        try:
            with sr.Microphone() as mic:
                print("DEBUG: Waiting for wake word...")
                
                try:
                    audio = recognizer.listen(mic, timeout=2, phrase_time_limit=4)
                    text = recognizer.recognize_google(audio).lower()
                except sr.WaitTimeoutError:
                    continue 
                except sr.UnknownValueError:
                    continue 
                
                # --- WAKE WORD DETECTED ---
                if state.wake_word.lower() in text:
                    print(f"DEBUG: Wake word '{text}' detected!")
                    update_status(page, status_control, "Listening for command...", is_active=True)
                    
                    speak_text("Yes?") 
                    
                    print("DEBUG: Listening for command NOW (Mic active)...")
                    
                    try:
                        audio_cmd = recognizer.listen(mic, timeout=7, phrase_time_limit=10)
                        command_text = recognizer.recognize_google(audio_cmd).lower()
                        
                        print(f"DEBUG: I heard command -> {command_text}")
                        speak_text("On it.")
                        
                        process_voice_command(command_text, page, status_control)
                        
                    except sr.WaitTimeoutError:
                        print("DEBUG: Command timeout.")
                        update_status(page, status_control, "Timed out. Idle.")
                        speak_text("I didn't hear anything.")
                        
                    except sr.UnknownValueError:
                        print("DEBUG: Command unintelligible.")
                        update_status(page, status_control, "Didn't understand. Idle.")
                        speak_text("I couldn't understand that.")

                    # Final Reset
                    update_status(page, status_control, "Idle - Assistant On")

        except Exception as e:
            print(f"General Loop Error: {e}")
            time.sleep(1)

# --- UI APPLICATION ---
def main(page: ft.Page):
    page.title = "Neon AI Assistant"
    page.theme_mode = ft.ThemeMode.DARK
    page.window_width = 450
    page.window_height = 800
    page.padding = 0
    
    page.fonts = {"Roboto": "https://fonts.googleapis.com/css2?family=Roboto:wght@400;700&display=swap"}
    page.theme = ft.Theme(font_family="Roboto", color_scheme_seed="cyan")

    # --- HELPER UI ---
    def get_gradient_container(content):
        return ft.Container(
            content=content,
            gradient=ft.LinearGradient(
                begin=ft.alignment.top_left,
                end=ft.alignment.bottom_right,
                colors=["#0f172a", "#000000"],
            ),
            expand=True,
            padding=20
        )

    # --- SCREEN 1: LOGIN ---
    def login_screen():
        email_field = ft.TextField(label="Email", border_radius=10, prefix_icon="email")
        pass_field = ft.TextField(label="Password", password=True, can_reveal_password=True, border_radius=10, prefix_icon="lock")
        
        def handle_login(e):
            user = db.login_user(email_field.value, pass_field.value)
            if user:
                state.current_user = user
                state.wake_word = user[8] 
                if state.wake_word == "hey alexa":
                    page.go("/setup")
                else:
                    page.go("/dashboard")
            else:
                page.open(ft.SnackBar(ft.Text("Invalid Credentials"), bgcolor="red"))

        return get_gradient_container(
            ft.Column([
                ft.Icon(name="auto_awesome", size=80, color="cyanAccent"),
                ft.Text("AI ASSISTANT", size=30, weight="bold", color="cyanAccent"),
                ft.Divider(height=50, color="transparent"),
                email_field,
                pass_field,
                ft.ElevatedButton("Login", on_click=handle_login, width=200, style=ft.ButtonStyle(color="black", bgcolor="cyanAccent")),
                ft.TextButton("Create Account", on_click=lambda _: page.go("/register"))
            ], alignment=ft.MainAxisAlignment.CENTER, horizontal_alignment=ft.CrossAxisAlignment.CENTER)
        )

    # --- SCREEN 2: REGISTER ---
    def register_screen():
        f_name = ft.TextField(label="First Name", expand=True)
        l_name = ft.TextField(label="Last Name", expand=True)
        age = ft.TextField(label="Age", width=100, keyboard_type=ft.KeyboardType.NUMBER)
        email = ft.TextField(label="Email", prefix_icon="email")
        password = ft.TextField(label="Password", password=True)
        
        institution_field = ft.TextField(label="Institution Name", visible=False)
        company_field = ft.TextField(label="Company Name", visible=False)

        def radio_change(e):
            val = e.control.value
            if val == "Student":
                institution_field.visible = True
                company_field.visible = False
            else:
                institution_field.visible = False
                company_field.visible = True
            page.update()

        occ_radio = ft.RadioGroup(
            content=ft.Row([
                ft.Radio(value="Student", label="Student"),
                ft.Radio(value="Employee", label="Employee")
            ]),
            on_change=radio_change
        )

        def handle_register(e):
            role = occ_radio.value
            place = institution_field.value if role == "Student" else company_field.value
            if not role or not email.value: return 
            
            success = db.register_user(email.value, password.value, f_name.value, l_name.value, int(age.value), role, place)
            if success:
                page.open(ft.SnackBar(ft.Text("Account Created! Please Login."), bgcolor="green"))
                page.go("/")
            else:
                page.open(ft.SnackBar(ft.Text("Email already exists."), bgcolor="red"))

        return get_gradient_container(
            ft.ListView([
                ft.Text("Create Account", size=30, weight="bold"),
                ft.Row([f_name, l_name]),
                age,
                email,
                password,
                ft.Text("Occupation:"),
                occ_radio,
                institution_field,
                company_field,
                ft.Divider(),
                ft.ElevatedButton("Register", on_click=handle_register, height=50),
                ft.TextButton("Back to Login", on_click=lambda _: page.go("/"))
            ], spacing=15)
        )

    # --- SCREEN 3: SETUP ---
    def setup_screen():
        wake_word_input = ft.TextField(label="Wake Word", value="Hey Alexa")
        
        def save_setup(e):
            if state.current_user:
                db.update_wake_word(state.current_user[0], wake_word_input.value)
                state.wake_word = wake_word_input.value
                page.go("/dashboard")

        return get_gradient_container(
            ft.Column([
                ft.Text("Setup Assistant", size=24, weight="bold"),
                wake_word_input,
                ft.ElevatedButton("Complete Setup", on_click=save_setup)
            ], alignment=ft.MainAxisAlignment.CENTER)
        )

    # --- SCREEN 4: DASHBOARD ---
    def dashboard_screen():
        user_name = state.current_user[3] if state.current_user else "User"
        status_text = ft.Text("Idle - Assistant Off", size=16, color="grey400", text_align=ft.TextAlign.CENTER)
        
        def show_profile(e):
            u = state.current_user
            if not u: return
            
            dlg = ft.AlertDialog(
                title=ft.Text("User Profile"),
                content=ft.Column([
                    ft.Text(f"Name: {u[3]} {u[4]}"),
                    ft.Text(f"Age: {u[5]}"),
                    ft.Text(f"Role: {u[6]}"),
                    ft.Text(f"Organization: {u[7]}"),
                    ft.Divider(),
                    ft.Text(f"Wake Word: {state.wake_word}", weight="bold", color="cyan")
                ], height=200, tight=True),
            )
            page.open(dlg)

        def toggle_listening(e):
            state.is_listening = e.control.value
            status_text.value = f"Listening for '{state.wake_word}'..." if state.is_listening else "Idle - Assistant Off"
            status_text.color = "cyan" if state.is_listening else "grey400"
            page.update()

        def clear_status(e):
            status_text.value = "Idle"
            status_text.color = "grey400"
            page.update()

        if not hasattr(state, 'thread_started'):
            t = threading.Thread(target=start_background_listener, args=(page, status_text), daemon=True)
            t.start()
            state.thread_started = True

        return get_gradient_container(
            ft.Column([
                ft.Row([
                    ft.Text("AI ASSISTANT", weight="bold", size=20, color="cyanAccent"),
                    ft.IconButton(icon="person", on_click=show_profile, bgcolor="#424242")
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                
                ft.Divider(color="transparent"),
                
                ft.Column([
                    ft.Text(f"Welcome, {user_name}", size=30, weight="bold"),
                    ft.Container(height=20),
                    ft.Container(
                        content=ft.Switch(label="Activate Assistant", value=state.is_listening, on_change=toggle_listening, active_color="cyanAccent"),
                        padding=20, border=ft.border.all(1, "#424242"), border_radius=20, bgcolor="#000000"
                    ),
                    ft.Container(height=30),
                    ft.Container(
                        content=ft.Column([
                            ft.Row([ft.Text("Current Response", weight="bold"), ft.IconButton(icon="close", icon_color="red", icon_size=20, on_click=clear_status)], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                            ft.Divider(),
                            status_text
                        ]),
                        padding=20, bgcolor="#212121", border_radius=15, expand=True 
                    )
                ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, expand=True)
            ])
        )

    # --- ROUTING (FIXED BLACK BACKGROUND) ---
    def route_change(e):
        page.views.clear()
        troute = page.route if page.route else "/"
        
        # We apply padding=0 and bgcolor="#000000" to ALL views to prevent the white glitch
        if troute == "/": 
            page.views.append(ft.View("/", [login_screen()], padding=0, bgcolor="#000000"))
        elif troute == "/register": 
            page.views.append(ft.View("/register", [register_screen()], padding=0, bgcolor="#000000"))
        elif troute == "/setup": 
            page.views.append(ft.View("/setup", [setup_screen()], padding=0, bgcolor="#000000"))
        elif troute == "/dashboard": 
            page.views.append(ft.View("/dashboard", [dashboard_screen()], padding=0, bgcolor="#000000"))
            
        page.update()

    def view_pop(e):
        page.views.pop()
        top_view = page.views[-1]
        page.go(top_view.route)

    # --- CRITICAL: ATTACH EVENTS ---
    page.on_route_change = route_change
    page.on_view_pop = view_pop
    
    # Start at login
    page.go("/")

ft.app(target=main)