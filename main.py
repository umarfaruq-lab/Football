import os
import time
import math
import sqlite3
import requests
from datetime import datetime, timedelta
import pandas as pd
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
import matplotlib
matplotlib.use('Agg') # Non-interactive backend for server environments
import matplotlib.pyplot as plt

# Environment Variables injected securely from GitHub Secrets
API_KEY = os.getenv("FOOTBALL_API_KEY")
EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

BASE_URL = "https://api.football-data.org/v4"
HEADERS = {"X-Auth-Token": API_KEY} if API_KEY else {}

# All 12 Free-Tier Competitions on football-data.org
LEAGUES = {
    "PL": "Premier League (England)",
    "ELC": "Championship (England)",
    "PD": "La Liga (Spain)",
    "SA": "Serie A (Italy)",
    "BL1": "Bundesliga (Germany)",
    "FL1": "Ligue 1 (France)",
    "DED": "Eredivisie (Netherlands)",
    "PPL": "Primeira Liga (Portugal)",
    "BSA": "Campeonato Brasileiro Série A (Brazil)",
    "CL": "UEFA Champions League (Europe)",
    "EC": "European Championship (Europe)",
    "WC": "FIFA World Cup (World)"
}

DB_FILE = "tracker.db"
CHART_FILE = "performance_chart.png"

def init_db():
    """Initializes the institutional SQLite database for robust historical tracking."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            league TEXT,
            match TEXT,
            prediction TEXT,
            confidence TEXT,
            actual TEXT,
            status TEXT
        )
    ''')
    conn.commit()
    conn.close()

def robust_api_get(url, retries=3, backoff_factor=2):
    """Robust API request wrapper with exponential backoff for rate limits and connection drops."""
    for attempt in range(retries):
        try:
            response = requests.get(url, headers=HEADERS, timeout=10)
            if response.status_code == 200:
                return response
            elif response.status_code == 429:
                sleep_time = backoff_factor * (attempt + 1)
                print(f"Rate limited (429). Retrying in {sleep_time}s...")
                time.sleep(sleep_time)
            else:
                print(f"API Error {response.status_code}: {response.text}")
                return response
        except Exception as e:
            sleep_time = backoff_factor * (attempt + 1)
            print(f"Request exception ({e}). Retrying in {sleep_time}s...")
            time.sleep(sleep_time)
    return None

def fetch_upcoming_matches():
    today = datetime.now().strftime("%Y-%m-%d")
    future_date = (datetime.now() + timedelta(days=4)).strftime("%Y-%m-%d")
    url = f"{BASE_URL}/matches?dateFrom={today}&dateTo={future_date}"
    
    response = robust_api_get(url)
    if not response or response.status_code != 200:
        return []
        
    matches = response.json().get("matches", [])
    filtered_matches = [m for m in matches if m.get("competition", {}).get("code") in LEAGUES]
    print(f"Found {len(filtered_matches)} upcoming matches across monitored leagues.")
    return filtered_matches

def fetch_league_standings(competition_code):
    url = f"{BASE_URL}/competitions/{competition_code}/standings"
    response = robust_api_get(url)
    if not response or response.status_code != 200:
        return {}
    standings_data = response.json().get("standings", [])
    for table in standings_data:
        if table.get("type") == "TOTAL":
            return {item["team"]["id"]: item for item in table.get("table", [])}
    return {}

def calculate_poisson_probabilities(exp_home, exp_away, max_goals=6):
    """Calculates exact match outcome percentages using Poisson distribution."""
    home_win_prob = 0.0
    draw_prob = 0.0
    away_win_prob = 0.0
    
    for i in range(max_goals + 1):
        for j in range(max_goals + 1):
            p_home = (math.pow(exp_home, i) * math.exp(-exp_home)) / math.factorial(i)
            p_away = (math.pow(exp_away, j) * math.exp(-exp_away)) / math.factorial(j)
            scoreline_prob = p_home * p_away
            
            if i > j:
                home_win_prob += scoreline_prob
            elif i == j:
                draw_prob += scoreline_prob
            else:
                away_win_prob += scoreline_prob
                
    return round(home_win_prob * 100, 1), round(draw_prob * 100, 1), round(away_win_prob * 100, 1)

def calculate_kelly_stake(confidence, bankroll=100.0):
    """Calculates suggested bankroll stake using fractional Kelly principles."""
    if confidence == "High":
        return round(bankroll * 0.02, 2)
    elif confidence == "Medium":
        return round(bankroll * 0.01, 2)
    else:
        return round(bankroll * 0.005, 2)

def analyze_and_predict(matches):
    predictions = []
    
    for match in matches:
        match_id = match["id"]
        comp_code = match["competition"]["code"]
        home_team = match["homeTeam"]["name"]
        away_team = match["awayTeam"]["name"]
        home_id = match["homeTeam"]["id"]
        away_id = match["awayTeam"]["id"]
        
        utc_date_str = match.get("utcDate", "")
        try:
            dt_obj = datetime.strptime(utc_date_str, "%Y-%m-%dT%H:%M:%SZ")
            kickoff_display = dt_obj.strftime("%b %d, %H:%M UTC")
            match_date_only = dt_obj.strftime("%Y-%m-%d")
        except:
            kickoff_display = "TBD"
            match_date_only = datetime.now().strftime("%Y-%m-%d")

        standings = fetch_league_standings(comp_code)
        home_stats = standings.get(home_id, {})
        away_stats = standings.get(away_id, {})
        
        home_played = max(home_stats.get("playedGames", 1), 1)
        away_played = max(away_stats.get("playedGames", 1), 1)
        
        # Expected Goals (xG) Calculation
        home_gf = home_stats.get("goalsFor", 0) / home_played
        home_ga = home_stats.get("goalsAgainst", 0) / home_played
        away_gf = away_stats.get("goalsFor", 0) / away_played
        away_ga = away_stats.get("goalsAgainst", 0) / away_played
        
        exp_home_goals = round((home_gf + away_ga) / 2, 2)
        exp_away_goals = round((away_gf + home_ga) / 2, 2)
        total_exp_goals = round(exp_home_goals + exp_away_goals, 2)
        
        # Poisson Distribution Probabilities (%)
        p_home, p_draw, p_away = calculate_poisson_probabilities(exp_home_goals, exp_away_goals)
        
        # Requested Markets & Filters
        btts = "Yes" if (exp_home_goals >= 0.85 and exp_away_goals >= 0.85) else "No"
        over_2_5 = "Yes" if total_exp_goals >= 2.5 else "Under 2.5"
        
        # Market 1: At least one team will score over 1.5 goals
        team_over_15 = "None"
        if exp_home_goals >= 1.5 and exp_away_goals >= 1.5:
            team_over_15 = "Both Teams (>1.5)"
        elif exp_home_goals >= 1.5:
            team_over_15 = f"{home_team} (>1.5)"
        elif exp_away_goals >= 1.5:
            team_over_15 = f"{away_team} (>1.5)"
        else:
            team_over_15 = "No Team (xG < 1.5)"

        # Mathematical Confidence Tiering & Double Chance Under 4.5 Rules
        if p_home >= 55.0:
            predicted_winner = home_team
            confidence = "High"
            base_dc = f"{home_team} or Draw (1X)"
        elif p_away >= 50.0:
            predicted_winner = away_team
            confidence = "High"
            base_dc = f"{away_team} or Draw (X2)"
        elif 42.0 <= p_home < 55.0:
            predicted_winner = home_team
            confidence = "Medium"
            base_dc = f"{home_team} or Draw (1X)"
        elif 40.0 <= p_away < 50.0:
            predicted_winner = away_team
            confidence = "Medium"
            base_dc = f"{away_team} or Draw (X2)"
        else:
            predicted_winner = "Draw / Closely Contested"
            confidence = "Low"
            base_dc = "Tight Match (12 or X)"

        # Market 2: Double Chance under 4.5 goals filter rule
        if total_exp_goals < 4.5:
            double_chance_under_45 = f"{base_dc} & Under 4.5 Goals"
        else:
            double_chance_under_45 = f"{base_dc} (High Goal Risk >= 4.5)"
            
        recommended_stake = calculate_kelly_stake(confidence)
            
        predictions.append({
            "date": match_date_only,
            "kickoff": kickoff_display,
            "league": LEAGUES.get(comp_code, comp_code),
            "match": f"{home_team} vs {away_team}",
            "prediction": predicted_winner,
            "confidence": confidence,
            "stake": f"${recommended_stake}",
            "dc_under_45": double_chance_under_45,
            "team_over_15": team_over_15,
            "btts": btts,
            "over_2_5": over_2_5,
            "poisson_odds": f"H:{p_home}% | D:{p_draw}% | A:{p_away}%",
            "exp_goals": f"{exp_home_goals} - {exp_away_goals} (Tot: {total_exp_goals})"
        })
        
    return predictions

def update_sqlite_tracker_and_evaluate(new_predictions):
    """Updates and evaluates prediction statuses using SQLite relational database."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Evaluate pending matches
    cursor.execute("SELECT id, date, match, prediction FROM predictions WHERE status = 'PENDING'")
    pending_rows = cursor.fetchall()
    
    if pending_rows:
        oldest_date = min([row[1] for row in pending_rows])
        today_str = datetime.now().strftime("%Y-%m-%d")
        url = f"{BASE_URL}/matches?dateFrom={oldest_date}&dateTo={today_str}"
        response = robust_api_get(url)
        
        if response and response.status_code == 200:
            finished_matches = response.json().get("matches", [])
            for row_id, match_date, match_desc, prediction in pending_rows:
                for m in finished_matches:
                    if m.get("status") == "FINISHED":
                        h_name = m["homeTeam"]["name"]
                        a_name = m["awayTeam"]["name"]
                        if f"{h_name} vs {a_name}" == match_desc:
                            full_time = m["score"]["fullTime"]
                            h_goals = full_time.get("home", 0)
                            a_goals = full_time.get("away", 0)
                            
                            if h_goals > a_goals: actual_winner = h_name
                            elif a_goals > h_goals: actual_winner = a_name
                            else: actual_winner = "Draw / Closely Contested"
                                
                            actual_str = f"{h_name} {h_goals}-{a_goals} {a_name}"
                            status = "WON" if prediction == actual_winner else "LOST"
                            
                            cursor.execute("""
                                UPDATE predictions 
                                SET actual = ?, status = ? 
                                WHERE id = ?
                            """, (actual_str, status, row_id))
                            break
        conn.commit()

    # Insert new predictions (avoiding duplicates)
    for p in new_predictions:
        cursor.execute("""
            SELECT id FROM predictions WHERE date = ? AND match = ?
        """, (p["date"], p["match"]))
        if not cursor.fetchone():
            cursor.execute("""
                INSERT INTO predictions (date, league, match, prediction, confidence, actual, status)
                VALUES (?, ?, ?, ?, ?, 'Pending', 'PENDING')
            """, (p["date"], p["league"], p["match"], p["prediction"], p["confidence"]))
            
    conn.commit()
    
    # Load entire tracker into pandas DataFrame for reporting & charting
    df = pd.read_sql_query("SELECT * FROM predictions", conn)
    conn.close()
    return df

def generate_performance_chart(tracker_df):
    completed = tracker_df[tracker_df["status"].isin(["WON", "LOST"])].copy()
    if len(completed) < 2:
        return None
        
    completed['ResultNum'] = completed['status'].apply(lambda x: 1 if x == 'WON' else -1)
    completed['Cumulative'] = completed['ResultNum'].cumsum()
    
    plt.figure(figsize=(8, 4))
    plt.plot(range(len(completed)), completed['Cumulative'], marker='o', color='#2b8a3e', linewidth=2, markersize=5)
    plt.title("Institutional SQLite Equity Curve", fontsize=12, fontweight='bold', color='#333333')
    plt.xlabel("Completed Matches", fontsize=10)
    plt.ylabel("Net Wins / Units", fontsize=10)
    plt.grid(True, linestyle='--', alpha=0.6)
    plt.tight_layout()
    
    plt.savefig(CHART_FILE, dpi=150)
    plt.close()
    return CHART_FILE

def calculate_analytics_summaries(tracker_df):
    completed = tracker_df[tracker_df["status"].isin(["WON", "LOST"])]
    if completed.empty:
        return "No completed matches evaluated yet.", "No completed matches evaluated yet."
    
    tier_summary = ""
    for tier in ["High", "Medium", "Low"]:
        tier_df = completed[completed["confidence"] == tier]
        total = len(tier_df)
        won = len(tier_df[tier_df["status"] == "WON"])
        rate = (won / total * 100) if total > 0 else 0.0
        tier_summary += f"• <b>{tier} Confidence:</b> {won}/{total} ({rate:.1f}%)\n"
        
    league_summary = ""
    for league, group in completed.groupby("league"):
        total = len(group)
        won = len(group[group["status"] == "WON"])
        rate = (won / total * 100) if total > 0 else 0.0
        league_summary += f"• <b>{league}:</b> {won}/{total} ({rate:.1f}%)\n"
        
    return tier_summary, league_summary

def send_telegram_alert(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"Telegram error: {e}")

def send_email(predictions, tracker_df):
    if not EMAIL_SENDER or not EMAIL_PASSWORD or not EMAIL_RECEIVER:
        print("Email credentials missing.")
        return

    completed = tracker_df[tracker_df["status"].isin(["WON", "LOST"])]
    total_completed = len(completed)
    total_won = len(completed[completed["status"] == "WON"])
    overall_accuracy = (total_won / total_completed * 100) if total_completed > 0 else 0.0

    tier_breakdown, league_breakdown = calculate_analytics_summaries(tracker_df)
    chart_path = generate_performance_chart(tracker_df)

    html_content = "<h2>⚽ Institutional SQLite Football Report</h2>"
    html_content += f"<p>Report generated on <b>{datetime.now().strftime('%Y-%m-%d')}</b> with SQLite persistence, Poisson models, and advanced market filters.</p>"
    
    html_content += f"<div style='background: #e9ecef; padding: 10px; border-radius: 5px; margin-bottom: 15px;'>"
    html_content += f"<b>Overall Accuracy:</b> {total_won}/{total_completed} ({overall_accuracy:.1f}%)<br><br>"
    html_content += f"<b>Performance by Tier:</b><br>{tier_breakdown.replace(chr(10), '<br>')}<br>"
    html_content += f"<b>Performance by League:</b><br>{league_breakdown.replace(chr(10), '<br>')}"
    html_content += "</div>"

    if chart_path and os.path.exists(chart_path):
        html_content += "<h3>📈 Model Equity Curve</h3>"
        html_content += "<p><img src='cid:performancechart' style='max-width: 100%; height: auto; border-radius: 5px;'></p>"

    if not predictions:
        html_content += "<p><b>No upcoming matches found in the next 4 days across monitored leagues.</b></p>"
        telegram_msg = "⚽ <b>SQLite Football Report</b>\nNo upcoming matches found in the 4-day window."
    else:
        html_content += "<h3>📅 Upcoming Fixtures, Double Chance (Under 4.5) & Team Over 1.5 Markets</h3>"
        html_content += "<table border='1' cellspacing='0' cellpadding='5' style='border-collapse: collapse; width: 100%; font-family: sans-serif; font-size: 11px;'>"
        html_content += "<tr style='background-color: #f8f9fa;'><th>Kickoff</th><th>League</th><th>Matchup</th><th>Poisson Prob</th><th>DC (&lt;4.5 Goals)</th><th>Team &gt;1.5</th><th>Stake</th></tr>"
        
        high_list, team_over15_list, dc_under45_list = [], [], []

        for p in predictions:
            html_content += f"<tr><td>{p['kickoff']}</td><td>{p['league']}</td><td>{p['match']}</td><td>{p['poisson_odds']}</td><td><b>{p['dc_under_45']}</b></td><td>{p['team_over_15']}</td><td>{p['stake']}</td></tr>"
            
            item_desc = f"<b>{p['match']}</b> ({p['league']}) ➔ DC & <4.5: <b>{p['dc_under_45']}</b> | Team >1.5: <b>{p['team_over_15']}</b>"
            if p['confidence'] == 'High': high_list.append(item_desc)
            if 'Both' in p['team_over_15'] or '(>1.5)' in p['team_over_15']: 
                team_over15_list.append(f"<b>{p['match']}</b> ➔ {p['team_over_15']}")
            if 'Under 4.5' in p['dc_under_45']:
                dc_under45_list.append(f"<b>{p['match']}</b> ➔ {p['dc_under_45']}")

        html_content += "</table>"

        html_content += "<h3 style='color: #2b8a3e;'>🔥 High Confidence Poisson Shortlist</h3><ul>" + ("".join([f"<li>{x}</li>" for x in high_list]) if high_list else "<li>None today.</li>") + "</ul>"
        html_content += "<h3 style='color: #1971c2;'>⚡ Team Over 1.5 Targets</h3><ul>" + ("".join([f"<li>{x}</li>" for x in team_over15_list]) if team_over15_list else "<li>None today.</li>") + "</ul>"
        html_content += "<h3>🛡️ Double Chance & Under 4.5 Goals Shortlist</h3><ul>" + ("".join([f"<li>{x}</li>" for x in dc_under45_list]) if dc_under45_list else "<li>None today.</li>") + "</ul>"

        telegram_msg = f"⚽ <b>SQLite Football Report</b>\n📊 <b>Accuracy:</b> {overall_accuracy:.1f}%\n"
        if high_list:
            telegram_msg += f"\n🔥 <b>High Conf Picks:</b>\n" + "\n".join([f"• {item}" for item in high_list[:3]])

    msg = MIMEMultipart("related")
    msg["Subject"] = f"Institutional SQLite Football Analytics - {datetime.now().strftime('%Y-%m-%d')}"
    msg["From"] = EMAIL_SENDER
    msg["To"] = EMAIL_RECEIVER
    
    msg_alternative = MIMEMultipart("alternative")
    msg.attach(msg_alternative)
    msg_alternative.attach(MIMEText(html_content, "html"))

    if chart_path and os.path.exists(chart_path):
        with open(chart_path, "rb") as img_file:
            msg_image = MIMEImage(img_file.read())
            msg_image.add_header('Content-ID', '<performancechart>')
            msg_image.add_header('Content-Disposition', 'inline', filename=CHART_FILE)
            msg.attach(msg_image)

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())
        print("Institutional SQLite email report sent successfully!")
    except Exception as e:
        print(f"Failed to send email: {e}")

    send_telegram_alert(telegram_msg)

if __name__ == "__main__":
    print("Initializing institutional SQLite database and running pipeline...")
    init_db()
    matches = fetch_upcoming_matches()
    predictions = analyze_and_predict(matches)
    tracker_df = update_sqlite_tracker_and_evaluate(predictions)
    send_email(predictions, tracker_df)
    print("Execution complete.")
