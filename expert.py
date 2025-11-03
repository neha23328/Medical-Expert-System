#!/usr/bin/env python3
"""
medical_expert_chatbot_best_match.py

Chat-style Tkinter GUI + Experta medical expert system.
If no exact rule matches, compute best-match (probability-like) suggestions
based on overlapping "yes" symptoms; show top 3 candidates and enable treatment
for top suggestion. No emergency question.
Session logged to sessions.csv.
"""

import tkinter as tk
from tkinter import scrolledtext
from experta import *
import threading
import webbrowser
import csv
from datetime import datetime
import os
import heapq

# ---------------------------
# GUI + synchronization layer
# ---------------------------

class ChatGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("AI Medical Expert — Chat Doctor")
        self.root.geometry("2020x640+0+5")
        self.root.configure(bg="#a1d2f8")

        # Chat area (scrolled)
        self.chat = scrolledtext.ScrolledText(root, wrap=tk.WORD, state="disabled",
                                              font=("Segoe UI", 11), bg="white")
        self.chat.place(relx=0.02, rely=0.02, relwidth=0.96, relheight=0.72)

        # Bottom interactive frame
        self.controls = tk.Frame(root, bg="#f4f7fb")
        self.controls.place(relx=0.02, rely=0.76, relwidth=0.96, relheight=0.22)

        # Top row in controls: yes/no buttons & open treatment
        self.btn_frame = tk.Frame(self.controls, bg="#f4f7fb")
        self.btn_frame.pack(fill="x", padx=10, pady=(6,4))

        self.yes_btn = tk.Button(self.btn_frame, text="Yes", width=10, command=lambda: self._answer("yes"),
                                 bg="#2E7D32", fg="white", state="disabled")
        self.yes_btn.pack(side="left", padx=(0,8))
        self.no_btn = tk.Button(self.btn_frame, text="No", width=10, command=lambda: self._answer("no"),
                                bg="#B00020", fg="white", state="disabled")
        self.no_btn.pack(side="left", padx=(0,8))

        self.open_btn = tk.Button(self.btn_frame, text="Open treatment", command=self._open_treatment,
                                  bg="#1976D2", fg="white", state="disabled")
        self.open_btn.pack(side="right")

        # Middle row: multi-select area (hidden normally)
        self.multi_frame = tk.Frame(self.controls, bg="#ff7fc7")
        self.multi_frame.pack(fill="both", expand=True, padx=10, pady=6)
        self.multi_widgets = []  # (widget, var, option)

        # Bottom row: free text entry & send
        self.entry_frame = tk.Frame(self.controls, bg="#f4f7fb")
        self.entry_frame.pack(fill="x", padx=10, pady=(0,6))
        self.entry = tk.Entry(self.entry_frame, font=("Segoe UI", 11))
        self.entry.pack(side="left", fill="x", expand=True, padx=(0,8))
        self.send_btn = tk.Button(self.entry_frame, text="Send", width=10, command=self._send_text)
        self.send_btn.pack(side="right")

        # internal state for ask/wait
        self._event = None
        self._current_ask = None
        self._current_options = None
        self._treatment_link = None

        # style constants
        self.user_bg = "#DCF8C6"
        self.bot_bg = "#FFFFFF"

        # prepare chat initial message
        self._append_bot("Hi — I'm the AI Medical Expert. I'll ask a few questions to help with a likely diagnosis. Please answer honestly. (Type in the box or use Yes/No or the options.)")

    # Chat helpers
    def _append(self, who, text):
        self.chat.configure(state="normal")
        if who == "bot":
            self.chat.insert("end", "AI: ", ("ai_label",))
            self.chat.insert("end", text + "\n\n", ("ai_text",))
        else:
            self.chat.insert("end", "You: ", ("you_label",))
            self.chat.insert("end", text + "\n\n", ("you_text",))
        self.chat.configure(state="disabled")
        self.chat.see("end")
        # tag config
        self.chat.tag_config("ai_label", foreground="#0B5FFF", font=("Segoe UI", 10, "bold"))
        self.chat.tag_config("ai_text", background=self.bot_bg, lmargin1=6, lmargin2=6, rmargin=6)
        self.chat.tag_config("you_label", foreground="#4B4B4B", font=("Segoe UI", 10, "bold"))
        self.chat.tag_config("you_text", background=self.user_bg, lmargin1=6, lmargin2=6, rmargin=6)

    def _append_bot(self, text):
        self._append("bot", text)

    def _append_user(self, text):
        self._append("user", text)

    # Public ask methods used by engine (they block until an answer is set)
    def ask_text(self, prompt):
        """
        Ask for free text. Returns the typed string.
        """
        return self._ask_blocking(prompt, mode="text")

    def ask_yes_no(self, prompt):
        """
        Ask yes/no. Returns "yes" or "no".
        """
        return self._ask_blocking(prompt, mode="yesno")

    def ask_multi(self, prompt, options):
        """
        Ask multi-select (checkbox). Returns list of selected options (or ["none"]).
        """
        return self._ask_blocking(prompt, mode="multi", options=options)

    def _ask_blocking(self, prompt, mode="text", options=None):
        """
        Central ask: schedule UI update on mainloop then wait for _event to be set by user action.
        """
        # ensure previous event is cleared
        if self._event is None:
            self._event = threading.Event()
        else:
            self._event.clear()

        # schedule UI update in main thread
        def ui_setup():
            # append bot question
            self._append_bot(prompt if mode != "multi" else f"{prompt} (choose any then click Send)")
            self._current_ask = prompt
            self._current_options = options
            # reset controls
            self.entry.configure(state="normal")
            self.entry.delete(0, tk.END)
            self.send_btn.configure(state="normal")
            # yes/no buttons active only for yesno
            if mode == "yesno":
                self.yes_btn.configure(state="normal")
                self.no_btn.configure(state="normal")
            else:
                self.yes_btn.configure(state="disabled")
                self.no_btn.configure(state="disabled")
            # prepare multi options widgets
            for w in self.multi_widgets:
                w[0].destroy()
            self.multi_widgets = []
            # show multi options if needed
            if mode == "multi" and options:
                # create checkbuttons
                for opt in options:
                    var = tk.IntVar(value=0)
                    chk = tk.Checkbutton(self.multi_frame, text=opt, variable=var, bg="#f4f7fb", anchor="w", justify="left")
                    chk.pack(anchor="w")
                    self.multi_widgets.append((chk, var, opt))
            self._treatment_link = None
            # focus entry
            self.entry.focus_set()

        self.root.after(0, ui_setup)

        # wait until user acts
        self._event.wait()
        # read answer stored in _answer_value or _multi_selected
        if mode == "text":
            ans = getattr(self, "_answer_value", "")
            return ans
        elif mode == "yesno":
            ans = getattr(self, "_answer_value", "no")
            return ans
        elif mode == "multi":
            selected = getattr(self, "_multi_selected", None)
            if selected is None:
                return ["none"]
            return selected

    # GUI callbacks triggered by user actions
    def _answer(self, val):
        # common for yes/no buttons
        # disable buttons after click
        self.yes_btn.configure(state="disabled")
        self.no_btn.configure(state="disabled")
        self._answer_value = val
        self._append_user(val)
        # set event to release engine
        if self._event:
            self._event.set()

    def _send_text(self):
        txt = self.entry.get().strip()
        # if there are multi widgets, collect them
        if self.multi_widgets:
            selected = [opt for (_, var, opt) in self.multi_widgets if var.get() == 1]
            if not selected:
                selected = ["none"]
            self._multi_selected = selected
            # append user choices
            self._append_user(", ".join(selected))
            # cleanup multi widgets
            for w in self.multi_widgets:
                w[0].destroy()
            self.multi_widgets = []
            # also clear entry
            self.entry.delete(0, tk.END)
            if self._event:
                self._event.set()
            return

        # normal text send
        if not txt:
            return
        self._append_user(txt)
        self.entry.delete(0, tk.END)
        self._answer_value = txt
        if self._event:
            self._event.set()

    # Treatment link handler
    def enable_treatment(self, disease_name):
        """
        Called by engine when a diagnosis is available.
        Enables the 'Open treatment' button (it will try to open local file Treatment/html/<disease>.html).
        """
        self._treatment_link = disease_name
        self.open_btn.configure(state="normal")

    def _open_treatment(self):
        if not self._treatment_link:
            return
        path = os.path.join("Treatment", "html", f"{self._treatment_link}.html")
        if os.path.exists(path):
            webbrowser.open(path, new=2)
        else:
            # fallback to Wikipedia search
            query = self._treatment_link.replace(" ", "+")
            webbrowser.open(f"https://en.wikipedia.org/w/index.php?search={query}", new=2)

    def end_chat_message(self, text):
        self._append_bot(text)

# ---------------------------
# Engine (experta rules) with best-match fallback
# ---------------------------

class MedicalExpertEngine(KnowledgeEngine):
    def __init__(self, gui: ChatGUI):
        super().__init__()
        self.gui = gui
        # store matched symptoms per disease for final logging
        self.matched_symptoms = []
        self.diagnosis = None
        self.user = {"name": "Unknown", "gender": "Unknown"}
        # collect all 'yes' symptoms during the flow
        self.yes_symptoms = set()

        # canonical disease profiles used for best-match fallback
        # symptom tokens must match tokens added to self.yes_symptoms
        self.disease_profiles = {
            "Arthritis": ["stiff_joints", "swollen_joints", "red_skin_around_joints", "reduced_movement", "tiredness", "joint_pain"],
            "Peptic Ulcer": ["severe_vomiting", "burning_stomach", "bloating", "nausea", "weight_loss", "abdominal_pain"],
            "Gastritis": ["normal_vomiting", "nausea", "fullness_upper_abdomen", "bloating_abdomen", "abdominal_pain", "indigestion", "gnawing_pain"],
            "Diabetes": ["fatigue", "extreme_thirst", "extreme_hunger", "frequent_urination", "weight_loss", "blurred_vision", "frequent_infections", "sores"],
            "Dehydration": ["fatigue", "extreme_thirst", "dizziness", "dark_urine", "lethargy", "dry_mouth", "less_frequent_urination"],
            "Hypothyroidism": ["fatigue", "muscle_weakness", "depression", "constipation", "feeling_cold", "dry_skin", "dry_hair", "weight_gain", "decreased_sweating", "slow_heart", "joint_pains", "hoarseness"],
            "Obesity": ["short_breath", "back_joint_pain", "high_sweating", "snoring", "sudden_physical", "tiredness"],
            "Anemia": ["short_breath", "chest_pain", "fatigue", "headache", "irregular_heartbeat", "weakness", "pale_skin", "dizziness"],
            "Coronary Arteriosclerosis": ["short_breath", "chest_pain", "arm_pains", "heaviness", "sweating", "dizziness", "burning_heart"],
            "Asthma": ["short_breath", "chest_pain", "cough", "wheezing", "sleep_trouble"],
            "Dengue": ["high_fever", "headache", "eye_pain", "muscle_pain", "joint_pain", "nausea", "rashes", "bleeding"],
            "Bronchitis": ["low_fever", "cough", "wheezing", "chills", "chest_tightness", "sore_throat", "body_aches", "headache", "breathlessness", "blocked_nose"],
            "Tuberculosis": ["fever", "chest_pain", "fatigue", "loss_of_appetite", "persistent_cough"],
            "Influenza": ["fever", "fatigue", "sore_throat", "weakness", "dry_cough", "muscle_ache", "chills", "nasal_congestion", "headache"],
            "Hepatitis": ["fever", "fatigue", "abdominal_pain", "flu_like", "dark_urine", "pale_stool", "weight_loss", "jaundice"],
            "Pneumonia": ["fever", "chest_pain", "short_breath", "nausea", "sweat_chills", "rapid_breath", "cough_phlegm", "diarrhea"],
            "Malaria": ["fever", "chills", "abdominal_pain", "nausea", "headache", "sweating", "cough", "weakness", "muscle_pain", "back_pain"],
            "AIDS": ["fever", "rashes", "headache", "muscle_ache", "sore_throat", "swollen_lymph", "diarrhea", "cough", "weight_loss", "night_sweat"],
            "Pancreatitis": ["nausea", "fever", "upper_abdominal_pain", "fast_heartbeat", "weight_loss", "oily_stool"],
            "COVID-19": ["fever", "fatigue", "short_breath", "nausea", "chills", "cough", "body_aches", "headache", "sore_throat", "diarrhea", "loss_of_taste_smell"],
        }

    # ---------- Helper that normalizes and records yes answers ----------
    def _record_yes(self, token):
        """
        token: canonical symptom token string to add to yes_symptoms
        called whenever a user answers 'yes' to a question
        """
        if token:
            self.yes_symptoms.add(token)

    # ---------- Rules ----------
    @DefFacts()
    def _start(self):
        yield Fact(action="start")

    @Rule(Fact(action="start"))
    def gather_identity(self):
        # ask name & gender
        name = self.gui.ask_text("What's your name?")
        gender = self.gui.ask_text("What's your gender? (m/f)")
        self.user["name"] = name if name else "Unknown"
        self.user["gender"] = gender if gender else "Unknown"
        self.declare(Fact(action="questionnaire"))

    @Rule(Fact(action="questionnaire"))
    def ask_basic(self):
        # Ask basic symptoms similar to original system
        v = self.gui.ask_yes_no("Do you suffer from red eyes?")
        self.declare(Fact(red_eyes=v))
        if v == "yes":
            self._record_yes("red_eyes")

        v = self.gui.ask_yes_no("Are you suffering from fatigue?")
        self.declare(Fact(fatigue=v))
        if v == "yes":
            self._record_yes("fatigue")

        v = self.gui.ask_yes_no("Are you having shortness of breath?")
        self.declare(Fact(short_breath=v))
        if v == "yes":
            self._record_yes("short_breath")

        v = self.gui.ask_yes_no("Are you having loss of appetite?")
        self.declare(Fact(appetite_loss=v))
        if v == "yes":
            self._record_yes("loss_of_appetite")

        fevers = self.gui.ask_multi("Do you suffer from fever? (select all that apply)", ["Normal Fever", "Low Fever", "High Fever"])
        if fevers and fevers[0] != "none":
            self.declare(Fact(fever="yes"))
            for f in fevers:
                token = f.lower().replace(" ", "_")
                # record fever-specific token
                if token == "normal_fever":
                    self._record_yes("fever")
                elif token == "low_fever":
                    self._record_yes("low_fever")
                elif token == "high_fever":
                    self._record_yes("high_fever")
                self.declare(Fact(token))
        else:
            self.declare(Fact(fever="no"))

    # -- Appetite-loss branch rules --
    @Rule(AND(Fact(appetite_loss="yes"), Fact(fever="no"), Fact(short_breath="no"), Fact(fatigue="no")))
    def appetite_branch(self):
        v = self.gui.ask_yes_no("Are you having any joint pains?")
        self.declare(Fact(joint_pain=v))
        if v == "yes":
            self._record_yes("joint_pain")

        vomits = self.gui.ask_multi("Did you have vomitings?", ["Severe Vomiting", "Normal Vomiting"])
        if vomits and vomits[0] != "none":
            self.declare(Fact(vomit="yes"))
            for v in vomits:
                token = v.lower().replace(" ", "_")
                if token == "severe_vomiting":
                    self._record_yes("severe_vomiting")
                if token == "normal_vomiting":
                    self._record_yes("normal_vomiting")
                self.declare(Fact(token))
        else:
            self.declare(Fact(vomit="no"))

    @Rule(AND(Fact(appetite_loss="yes"), Fact(fever="no"), Fact(short_breath="no"),
              Fact(fatigue="no"), Fact(joint_pain="yes")))
    def rule_arthritis(self):
        questions = [
            ("Are you having stiff Joints?", "stiff_joints"),
            ("Are you experiencing swollen Joints?", "swollen_joints"),
            ("Did the skin turn red around the Joints?", "red_skin_around_joints"),
            ("Did the range of motion decrease at the Joints?", "reduced_movement"),
            ("Are you feeling tired even if you walk a small distance?", "tiredness"),
        ]
        checks = []
        for q, token in questions:
            ans = self.gui.ask_yes_no(q)
            checks.append(ans)
            if ans == "yes":
                self._record_yes(token)
        if sum(1 for v in checks if v == "yes") >= 3:
            syms = ["Stiff joints", "Swelling in joints", "Joint Pains", "Red skin around joints",
                    "Tiredness", "Reduced Movement near joints", "Appetite loss"]
            self._finalize("Arthritis", syms)

    @Rule(AND(Fact(appetite_loss="yes"), Fact(fever="no"), Fact(short_breath="no"),
              Fact(fatigue="no"), Fact("severe_vomiting")))
    def rule_peptic(self):
        questions = [
            ("Is your stomach having burning sensation?", "burning_stomach"),
            ("Are you having a feeling of fullness, bloating or belching?", "bloating"),
            ("Are you having mild Nausea?", "nausea"),
            ("Did you lose weight?", "weight_loss"),
            ("Are you having an intense and localized abdominal pain?", "abdominal_pain"),
        ]
        checks = []
        for q, token in questions:
            ans = self.gui.ask_yes_no(q)
            checks.append(ans)
            if ans == "yes":
                self._record_yes(token)
        if sum(1 for v in checks if v == "yes") >= 3:
            syms = ["Appetite loss", "Severe Vomiting", "Burning sensation in stomach",
                    "Bloated stomach", "Nausea", "Weight loss", "Abdominal pain"]
            self._finalize("Peptic Ulcer", syms)

    @Rule(AND(Fact(appetite_loss="yes"), Fact(fever="no"), Fact(short_breath="no"),
              Fact(fatigue="no"), Fact("normal_vomiting")))
    def rule_gastritis(self):
        questions = [
            ("Are you having a feeling of vomiting (Nausea)?", "nausea"),
            ("Are you having a feeling of fullness in your upper abdomen?", "fullness_upper_abdomen"),
            ("Are you feeling bloating in your abdomen?", "bloating_abdomen"),
            ("Are you having pain near abdomen?", "abdominal_pain"),
            ("Are you facing problems of indigestion?", "indigestion"),
            ("Are you experiencing a gnawing or burning ache or pain in your upper abdomen?", "gnawing_pain"),
        ]
        checks = []
        for q, token in questions:
            ans = self.gui.ask_yes_no(q)
            checks.append(ans)
            if ans == "yes":
                self._record_yes(token)
        if sum(1 for v in checks if v == "yes") >= 4:
            syms = ["Appetite loss", "Vomiting", "Nausea", "Fullness near abdomen", "Bloating near abdomen",
                    "Abdominal pain", "Indigestion", "Gnawing pain near abdomen"]
            self._finalize("Gastritis", syms)

    # -- Fatigue branch --
    @Rule(AND(Fact(fatigue="yes"), Fact(fever="no"), Fact(short_breath="no")))
    def fatigue_branch(self):
        v = self.gui.ask_yes_no("Are you feeling extremely thirsty than before?")
        self.declare(Fact(extreme_thirst=v))
        if v == "yes":
            self._record_yes("extreme_thirst")

        v = self.gui.ask_yes_no("Are you feeling extremely hungry than before?")
        self.declare(Fact(extreme_hunger=v))
        if v == "yes":
            self._record_yes("extreme_hunger")

        v = self.gui.ask_yes_no("Are you feeling dizzy?")
        self.declare(Fact(dizziness=v))
        if v == "yes":
            self._record_yes("dizziness")

        v = self.gui.ask_yes_no("Are your muscles weaker than before?")
        self.declare(Fact(muscle_weakness=v))
        if v == "yes":
            self._record_yes("muscle_weakness")

    @Rule(AND(Fact(fatigue="yes"), Fact(fever="no"), Fact(short_breath="no"),
              Fact(extreme_thirst="yes"), Fact(extreme_hunger="yes")))
    def rule_diabetes(self):
        questions = [
            ("Is your urination more frequent than before?", "frequent_urination"),
            ("Did you lose weight unintentionally?", "weight_loss"),
            ("Are you more irritable nowadays?", "irritability"),
            ("Did your vision get blurred?", "blurred_vision"),
            ("Are you having frequent infections such as gum or skin infections?", "frequent_infections"),
            ("Are your sores healing slowly?", "sores"),
        ]
        checks = []
        for q, token in questions:
            ans = self.gui.ask_yes_no(q)
            checks.append(ans)
            if ans == "yes":
                self._record_yes(token)
        if sum(1 for v in checks if v == "yes") >= 4:
            syms = ["Fatigue", "Extreme thirst", "Extreme hunger", "Weight loss", "Blurred vision", "Frequent infections", "Frequent urination", "Irritability", "Slow healing of sores"]
            self._finalize("Diabetes", syms)

    @Rule(AND(Fact(fatigue="yes"), Fact(fever="no"), Fact(short_breath="no"),
              Fact(extreme_thirst="yes"), Fact(dizziness="yes")))
    def rule_dehydration(self):
        questions = [
            ("Are you having less frequent urination?", "less_frequent_urination"),
            ("Is your urine dark?", "dark_urine"),
            ("Are you feeling lethargic?", "lethargy"),
            ("Is your mouth considerably dry?", "dry_mouth"),
        ]
        checks = []
        for q, token in questions:
            ans = self.gui.ask_yes_no(q)
            checks.append(ans)
            if ans == "yes":
                self._record_yes(token)
        if sum(1 for v in checks if v == "yes") >= 2:
            syms = ["Fatigue", "Extreme thirst", "Dizziness", "Dark urine", "Lethargy", "Dry mouth", "Less frequent urination"]
            self._finalize("Dehydration", syms)

    @Rule(AND(Fact(fatigue="yes"), Fact(fever="no"), Fact(short_breath="no"), Fact(muscle_weakness="yes")))
    def rule_hypothyroid(self):
        questions = [
            ("Are you feeling depressed nowadays?", "depression"),
            ("Are you experiencing constipation?", "constipation"),
            ("Are you feeling cold?", "feeling_cold"),
            ("Has your skin become drier?", "dry_skin"),
            ("Is your hair becoming dry or thinner?", "dry_hair"),
            ("Did you gain weight?", "weight_gain"),
            ("Are you not sweating much as earlier?", "decreased_sweating"),
            ("Did your heart rate slow down?", "slow_heart"),
            ("Are you experiencing pain and stiffness in joints?", "joint_pains"),
            ("Is your voice changing / hoarse?", "hoarseness"),
        ]
        checks = []
        for q, token in questions:
            ans = self.gui.ask_yes_no(q)
            checks.append(ans)
            if ans == "yes":
                self._record_yes(token)
        if sum(1 for v in checks if v == "yes") >= 7:
            syms = ["Fatigue", "Muscle weakness", "Depression", "Constipation", "Cold feeling", "Dry skin", "Dry hair", "Weight gain", "Decreased sweating", "Slow heart rate", "Joint pains", "Hoarseness in voice"]
            self._finalize("Hypothyroidism", syms)

    # -- Shortness of breath branch --
    @Rule(AND(Fact(short_breath="yes"), Fact(fever="no")))
    def breath_branch(self):
        v = self.gui.ask_yes_no("Are you having back and joint pain?")
        self.declare(Fact(back_joint_pain=v))
        if v == "yes":
            self._record_yes("back_joint_pain")

        v = self.gui.ask_yes_no("Are you having chest pain?")
        self.declare(Fact(chest_pain=v))
        if v == "yes":
            self._record_yes("chest_pain")

        v = self.gui.ask_yes_no("Are you having cough frequently?")
        self.declare(Fact(cough=v))
        if v == "yes":
            self._record_yes("cough")

        v = self.gui.ask_yes_no("Are you feeling fatigue?")
        self.declare(Fact(fatigue=v))
        if v == "yes":
            self._record_yes("fatigue")

        v = self.gui.ask_yes_no("Are you having headache?")
        self.declare(Fact(headache=v))
        if v == "yes":
            self._record_yes("headache")

        v = self.gui.ask_yes_no("Are you having pain in arms and shoulders?")
        self.declare(Fact(pain_arms=v))
        if v == "yes":
            self._record_yes("arm_pains")

    @Rule(AND(Fact(short_breath="yes"), Fact(fever="no"), Fact(back_joint_pain="yes")))
    def rule_obesity(self):
        questions = [
            ("Are you sweating more than normal?", "high_sweating"),
            ("Did you develop a habit of snoring?", "snoring"),
            ("Are you not able to cope up with sudden physical activity?", "sudden_physical"),
            ("Are you feeling tired every day without doing much work?", "tiredness"),
            ("Are you feeling isolated?", "isolated"),
            ("Are you having low confidence and self esteem?", "low_confidence"),
        ]
        checks = []
        for q, token in questions:
            ans = self.gui.ask_yes_no(q)
            checks.append(ans)
            if ans == "yes":
                self._record_yes(token)
        if sum(1 for v in checks if v == "yes") >= 4:
            syms = ["Shortness in breath", "Back and Joint pains", "High sweating", "Snoring habit", "Tireness", "Low confidence"]
            self._finalize("Obesity", syms)

    @Rule(AND(Fact(short_breath="yes"), Fact(fever="no"), Fact(chest_pain="yes"), Fact(fatigue="yes"), Fact(headache="yes")))
    def rule_anemia(self):
        questions = [
            ("Are you experiencing irregular heartbeat?", "irregular_heartbeat"),
            ("Are you feeling weak?", "weakness"),
            ("Has your skin turned pale or yellowish?", "pale_skin"),
            ("Are you having dizziness or light headedness?", "lightheadedness"),
            ("Are you having cold hands and feet?", "cold_hands_feet"),
        ]
        checks = []
        for q, token in questions:
            ans = self.gui.ask_yes_no(q)
            checks.append(ans)
            if ans == "yes":
                self._record_yes(token)
        if sum(1 for v in checks if v == "yes") >= 3:
            syms = ["Shortness in breath", "Chest pain", "Fatigue", "Headache", "Irregular heartbeat", "Weakness", "Pale skin", "Dizziness", "Cold limbs"]
            self._finalize("Anemia", syms)

    @Rule(AND(Fact(short_breath="yes"), Fact(fever="no"), Fact(chest_pain="yes"), Fact(pain_arms="yes")))
    def rule_cad(self):
        questions = [
            ("Did you have a feeling of heaviness or tightness in the chest?", "heaviness"),
            ("Are you sweating frequently?", "sweating"),
            ("Are you feeling dizzy?", "dizziness"),
            ("Do you feel burning sensation near heart?", "burning_heart"),
        ]
        checks = []
        for q, token in questions:
            ans = self.gui.ask_yes_no(q)
            checks.append(ans)
            if ans == "yes":
                self._record_yes(token)
        if sum(1 for v in checks if v == "yes") >= 2:
            syms = ["Shortness in breath", "Chest pain", "Fatigue", "Arm pains", "Heaviness", "Sweating", "Diziness", "Burning sensation near heart"]
            self._finalize("Coronary Arteriosclerosis", syms)

    @Rule(AND(Fact(short_breath="yes"), Fact(fever="no"), Fact(chest_pain="yes"), Fact(cough="yes")))
    def rule_asthma(self):
        questions = [
            ("Are you having a whistling or wheezing sound when exhaling?", "wheezing"),
            ("Are you having trouble sleeping caused by shortness of breath, coughing or wheezing?", "sleep_trouble"),
        ]
        checks = []
        for q, token in questions:
            ans = self.gui.ask_yes_no(q)
            checks.append(ans)
            if ans == "yes":
                self._record_yes(token)
        if sum(1 for v in checks if v == "yes") >= 1:
            syms = ["Shortness in breath", "Chest pain", "Cough", "Wheezing sound when exhaling", "Trouble sleep because of coughing or wheezing"]
            self._finalize("Asthma", syms)

    # -- Fever-specific rules (from fever facts) --
    @Rule(Fact("high_fever"))
    def rule_dengue(self):
        questions = [
            ("Are you experiencing severe headache?", "headache"),
            ("Are you having pain behind eyes?", "eye_pain"),
            ("Are you having severe muscle pain?", "muscle_pain"),
            ("Are you having severe joint pain?", "joint_pain"),
            ("Have you vomited or felt like vomiting (Nausea)?", "nausea"),
            ("Have you experienced rashes on skin appearing 2-5 days after fever onset?", "rashes"),
            ("Are you having mild bleeding such a nose bleed or bleeding gums?", "bleeding"),
        ]
        checks = []
        for q, token in questions:
            ans = self.gui.ask_yes_no(q)
            checks.append(ans)
            if ans == "yes":
                self._record_yes(token)
        if sum(1 for v in checks if v == "yes") >= 5:
            syms = ["High fever", "Headache", "Eye pain", "Muscle pain", "Joint pains", "Nausea", "Rashes", "Bleeding"]
            self._finalize("Dengue", syms)

    @Rule(Fact("low_fever"))
    def rule_bronchitis(self):
        questions = [
            ("Are you having a persistent cough which may produce yellow/grey mucus?", "cough"),
            ("Are you experiencing Wheezing?", "wheezing"),
            ("Are you experiencing chills?", "chills"),
            ("Are you having a feeling of tightness in the chest?", "chest_tightness"),
            ("Are you having a sore throat?", "sore_throat"),
            ("Are you having body pains?", "body_aches"),
            ("Are you experiencing breathlessness?", "breathlessness"),
            ("Are you having headache?", "headache"),
            ("Are you having a blocked nose or sinuses?", "blocked_nose"),
        ]
        checks = []
        for q, token in questions:
            ans = self.gui.ask_yes_no(q)
            checks.append(ans)
            if ans == "yes":
                self._record_yes(token)
        if sum(1 for v in checks if v == "yes") >= 7:
            syms = ["Slight Fever", "Cough", "Wheezing", "Chills", "Tightness in chest", "Sore throat", "Body aches", "Headache", "Breathlessness", "Blocked nose"]
            self._finalize("Bronchitis", syms)

    @Rule(AND(Fact("normal_fever")))
    def related_to_normal_fever(self):
        # ask general fever related
        v = self.gui.ask_yes_no("Are you suffering from chest pain?")
        self.declare(Fact(chest_pain=v))
        if v == "yes":
            self._record_yes("chest_pain")

        v = self.gui.ask_yes_no("Are you suffering from abdominal pain?")
        self.declare(Fact(abdominal_pain=v))
        if v == "yes":
            self._record_yes("abdominal_pain")

        v = self.gui.ask_yes_no("Are you suffering from sore throat?")
        self.declare(Fact(sore_throat=v))
        if v == "yes":
            self._record_yes("sore_throat")

        v = self.gui.ask_yes_no("Are you having shaking chills?")
        self.declare(Fact(chills=v))
        if v == "yes":
            self._record_yes("chills")

        v = self.gui.ask_yes_no("Are you suffering from rashes on skin?")
        self.declare(Fact(rashes=v))
        if v == "yes":
            self._record_yes("rashes")

        v = self.gui.ask_yes_no("Did you vomit or feel like vomiting (Nausea)?")
        self.declare(Fact(nausea=v))
        if v == "yes":
            self._record_yes("nausea")

    @Rule(AND(Fact("normal_fever"), Fact(chest_pain="yes"), Fact(fatigue="yes"), Fact(chills="yes")))
    def rule_tb(self):
        questions = [
            ("Are you experiencing persistent cough which lasted more than 2 to 3 weeks?", "persistent_cough"),
            ("Did you experience unintentional weight loss?", "weight_loss"),
            ("Are you experiencing Night Sweats?", "night_sweats"),
            ("Are you coughing up blood?", "cough_blood"),
        ]
        checks = []
        for q, token in questions:
            ans = self.gui.ask_yes_no(q)
            checks.append(ans)
            if ans == "yes":
                self._record_yes(token)
        if sum(1 for v in checks if v == "yes") >= 2:
            syms = ["fever", "chest pain", "fatigue", "loss of appetite", "persistent cough"]
            self._finalize("Tuberculosis", syms)

    @Rule(AND(Fact("normal_fever"), Fact(fatigue="yes"), Fact(sore_throat="yes")))
    def rule_influenza(self):
        questions = [
            ("Are you experiencing weakness?", "weakness"),
            ("Are you having dry persistent cough?", "dry_cough"),
            ("Are you having aching muscles?", "muscle_ache"),
            ("Are you experiencing sweats along with chills?", "chills"),
            ("Are you experiencing nasal congestion?", "nasal_congestion"),
            ("Are you experiencing headache?", "headache"),
        ]
        checks = []
        for q, token in questions:
            ans = self.gui.ask_yes_no(q)
            checks.append(ans)
            if ans == "yes":
                self._record_yes(token)
        if sum(1 for v in checks if v == "yes") >= 4:
            syms = ["Fever", "Fatigue", "Sore throat", "Weakness", "Dry cough", "Muscle aches", "Chills", "Nasal congestion", "Headache"]
            self._finalize("Influenza", syms)

    @Rule(AND(Fact("normal_fever"), Fact(fatigue="yes"), Fact(abdominal_pain="yes")))
    def rule_hepatitis(self):
        questions = [
            ("Are you experiencing flu-like symptoms?", "flu_like"),
            ("Are you getting dark urine?", "dark_urine"),
            ("Are you having pale stool?", "pale_stool"),
            ("Are you experiencing unexplained weight loss?", "weight_loss"),
            ("Are your skin and eyes turning yellow?", "jaundice"),
        ]
        checks = []
        for q, token in questions:
            ans = self.gui.ask_yes_no(q)
            checks.append(ans)
            if ans == "yes":
                self._record_yes(token)
        if sum(1 for v in checks if v == "yes") >= 3:
            syms = ["Fever", "Fatigue", "Abdominal pain", "Flu-like symptoms", "Dark urine", "Pale stool", "Weight loss", "Jaundice"]
            self._finalize("Hepatitis", syms)

    @Rule(AND(Fact("normal_fever"), Fact(chest_pain="yes"), Fact(short_breath="yes"), Fact(nausea="yes")))
    def rule_pneumonia(self):
        questions = [
            ("Are you experiencing shortness of breath while doing normal activities or while resting?", "short_breath"),
            ("Are you experiencing sweating along with chills?", "sweat_chills"),
            ("Are you breathing rapidly?", "rapid_breath"),
            ("Are you having a worsening cough that may produce yellow/green or bloody mucus?", "cough_phlegm"),
            ("Are you experiencing Diarrhea?", "diarrhea"),
        ]
        checks = []
        for q, token in questions:
            ans = self.gui.ask_yes_no(q)
            checks.append(ans)
            if ans == "yes":
                self._record_yes(token)
        if sum(1 for v in checks if v == "yes") >= 3:
            syms = ["Fever", "Chest pain", "Shortness in breath", "Nausea", "Sweating with chills", "Rapid breathing", "Cough with phlegm", "Diarrhea"]
            self._finalize("Pneumonia", syms)

    @Rule(AND(Fact("normal_fever"), Fact(chills="yes"), Fact(abdominal_pain="yes"), Fact(nausea="yes")))
    def rule_malaria(self):
        questions = [
            ("Are you experiencing headache?", "headache"),
            ("Are you experiencing sweating frequently?", "sweating"),
            ("Are you coughing frequently?", "cough"),
            ("Are you experiencing weakness?", "weakness"),
            ("Are you having intense muscle pain?", "muscle_pain"),
            ("Are you having lower back pain?", "back_pain"),
        ]
        checks = []
        for q, token in questions:
            ans = self.gui.ask_yes_no(q)
            checks.append(ans)
            if ans == "yes":
                self._record_yes(token)
        if sum(1 for v in checks if v == "yes") >= 4:
            syms = ["Fever", "Chills", "Abdominal pain", "Nausea", "Headache", "Sweating", "Cough", "Weakness", "Muscle pain", "Back pain"]
            self._finalize("Malaria", syms)

    @Rule(AND(Fact("normal_fever"), Fact(rashes="yes")))
    def rule_hiv(self):
        questions = [
            ("Are you experiencing headache?", "headache"),
            ("Are you having muscle aches and joint pain?", "muscle_ache"),
            ("Are you experiencing sore throat and painful mouth sores?", "sore_throat"),
            ("Are you experiencing swollen lymph glands especially on the neck?", "swollen_lymph"),
            ("Are you experiencing Diarrhea?", "diarrhea"),
            ("Are you coughing frequently?", "cough"),
            ("Did you experience unintentional weight loss?", "weight_loss"),
            ("Are you experiencing Night Sweats?", "night_sweats"),
        ]
        checks = []
        for q, token in questions:
            ans = self.gui.ask_yes_no(q)
            checks.append(ans)
            if ans == "yes":
                self._record_yes(token)
        if sum(1 for v in checks if v == "yes") >= 6:
            syms = ["Fever", "Rashes", "Headache", "Muscle ache", "Sore throat", "Swollen lymph nodes", "Diarrhea", "Cough", "Weight loss", "Night sweat"]
            self._finalize("AIDS", syms)

    @Rule(AND(Fact("normal_fever"), Fact(nausea="yes")))
    def rule_pancreatitis(self):
        questions = [
            ("Are you experiencing upper abdominal pain?", "upper_abdominal_pain"),
            ("Is the abdominal pain worse after eating?", "abdominal_worse_after_eating"),
            ("Is your heartbeat at high rate?", "fast_heartbeat"),
            ("Did you experience unintentional weight loss?", "weight_loss"),
            ("Are you having oily smelly stools?", "oily_stool"),
        ]
        checks = []
        for q, token in questions:
            ans = self.gui.ask_yes_no(q)
            checks.append(ans)
            if ans == "yes":
                self._record_yes(token)
        if sum(1 for v in checks if v == "yes") >= 3:
            syms = ["Nausea", "Fever", "Upper abdominal pain", "Fast heartbeat", "Weight loss", "Oily and smelly stool"]
            self._finalize("Pancreatitis", syms)

    @Rule(AND(Fact("normal_fever"), Fact(fatigue="yes"), Fact(short_breath="yes"), Fact(nausea="yes")))
    def rule_covid(self):
        questions = [
            ("Are you having chills sometimes with shaking?", "chills"),
            ("Do you cough frequently?", "cough"),
            ("Are you having body aches?", "body_aches"),
            ("Are you experiencing headache?", "headache"),
            ("Are you experiencing sore throat and mouth soreness?", "sore_throat"),
            ("Did you lose your sense of smell and taste considerably?", "loss_of_taste_smell"),
            ("Are you experiencing Diarrhea?", "diarrhea"),
        ]
        checks = []
        for q, token in questions:
            ans = self.gui.ask_yes_no(q)
            checks.append(ans)
            if ans == "yes":
                self._record_yes(token)
        if sum(1 for v in checks if v == "yes") >= 4:
            syms = ["Fever", "Fatigue", "Shortness in breath", "Nausea", "Chills", "Cough", "Body aches", "Headache", "Sore throat", "Diarrhea", "Loss of taste/smell"]
            self._finalize("COVID-19", syms)

    # Fallback: if no exact rule fired, compute best matches
    @Rule(AS.f << Fact(action="questionnaire"), salience=-100)
    def fallback_best_match(self, f):
        # compute best matches using collected yes_symptoms
        top = self._compute_best_matches()
        if not top:
            # no yes symptoms: final no match
            self._finalize("No match", ["Symptoms did not match known patterns — consult a physician"])
            return

        # top is list of (score, disease, matched_list)
        # show results to user
        lines = []
        for score, disease, matched in top:
            pct = int(score * 100)
            lines.append(f"{disease} ({pct}% match) — matched: {', '.join(matched) if matched else 'none'}")
        msg = "I couldn't find an exact rule match. Here are the most likely diagnoses (best-first):\n\n" + "\n".join(lines)
        # present the top suggestion prominently and enable treatment for it
        top_score, top_disease, top_matched = top[0]
        # finalize but mark that this was from best-match: don't raise SystemExit immediately, just finalize with info
        self.diagnosis = top_disease
        self.matched_symptoms = top_matched
        self.gui.end_chat_message("Best matches:\n - " + "\n - ".join(lines))
        # enable treatment for top disease
        self.gui.enable_treatment(top_disease)
        # log session
        self._log_session()
        # stop engine gracefully
        raise SystemExit()

    def _compute_best_matches(self, top_k=3):
        """
        Compute best-matching diseases from disease_profiles.
        Score = intersection_size / disease_profile_size (simple fractional match).
        Returns list of up to top_k tuples: (score, disease, matched_symptoms_list)
        """
        yes_set = set(self.yes_symptoms)
        if not yes_set:
            return []

        heap = []
        for disease, profile in self.disease_profiles.items():
            profile_set = set(profile)
            matched = sorted(list(profile_set.intersection(yes_set)))
            if not profile_set:
                continue
            score = (len(matched) / len(profile_set))
            # push negative score for max-heap behavior using heapq
            heapq.heappush(heap, (-score, disease, matched))

        if not heap:
            return []

        results = []
        for _ in range(min(top_k, len(heap))):
            neg_score, disease, matched = heapq.heappop(heap)
            results.append((-neg_score, disease, matched))
        return results

    # finalize helper (unchanged from previous behavior)
    def _finalize(self, disease_name, symptoms_list):
        # store diagnosis and show to user
        self.diagnosis = disease_name
        self.matched_symptoms = symptoms_list
        # append to chat and enable treatment
        self.gui.end_chat_message(f"Diagnosis: {disease_name}\nMatched symptoms:\n - " + "\n - ".join(symptoms_list))
        # enable treatment button
        self.gui.enable_treatment(disease_name)
        # log the session
        self._log_session()
        # Stop the engine by raising SystemExit so experta thread ends
        raise SystemExit()

    def _log_session(self):
        # write to sessions.csv
        fname = "sessions.csv"
        header = ["timestamp", "name", "gender", "diagnosis", "matched_symptoms", "yes_symptoms"]
        row = [datetime.now().isoformat(), self.user.get("name", ""), self.user.get("gender", ""), self.diagnosis, ";".join(self.matched_symptoms), ";".join(sorted(self.yes_symptoms))]
        write_header = not os.path.exists(fname)
        try:
            with open(fname, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                if write_header:
                    writer.writerow(header)
                writer.writerow(row)
        except Exception as e:
            # logging should not break UI; append small message
            self.gui.end_chat_message(f"(Session logging failed: {e})")

# ---------------------------
# Runner (engine thread + UI)
# ---------------------------

def start_engine(gui: ChatGUI):
    try:
        engine = MedicalExpertEngine(gui)
        engine.reset()
        engine.run()
    except SystemExit:
        # Normal termination after diagnosis (exact or best-match)
        gui.end_chat_message("✅ Diagnosis session finished. You can open treatment info or close the app.")
    except Exception as e:
        gui.end_chat_message(f"System: An error occurred: {e}")

def main():
    root = tk.Tk()
    gui = ChatGUI(root)

    # Start engine in a daemon thread so GUI stays responsive
    t = threading.Thread(target=start_engine, args=(gui,), daemon=True)
    t.start()

    root.mainloop()

if __name__ == "__main__":
    main()
