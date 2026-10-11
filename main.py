import os
import requests
from datetime import datetime, timedelta
import pandas as pd
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

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

TRACKER_FILE = "tracker.csv"

def fetch_upcoming_matches():
    today = datetime.now().strftime("%Y-%m-%d")
    future_date = (datetime.now() + timedelta(days=4)).strftime("%Y-%m-%d")
    
    url = f"{BASE_URL}/matches?dateFrom={today}&dateTo={future_date}"
    response = requests.get(url, headers=HEADERS)
    
    if response.status_code != 200:
        print(f"Error fetching matches: {response.status_code} - {response.text}")
        return []
        
    matches = response.json().get("matches", [])
    filtered_matches = [m for m in matches if m.get("competition", {}).get("code") in LEAGUES]
    print(f"Found {len(filtered_matches)} upcoming matches across all 12 free leagues.")
    return filtered_matches

def fetch_league_standings(competition_code):
    url = f"{BASE_URL}/competitions/{competition_code}/standings"
    response = requests.get(url, headers=HEADERS)
    if response.status_code != 200:
        return {}
    standings_data = response.json().get("standings", [])
    for table in standings_data:
        if table.get("type") == "TOTAL":
            return {item["team"]["id"]: item for item in table.get("table", [])}
    return {}

def fetch_h2h_data(match_id):
    url = f"{BASE_URL}/matches/{match_id}/head2head"
    response = requests.get(url, headers=HEADERS)
    if response.status_code != 200:
        return None
    return response.json().get("aggregates", {})

def parse_form_score(form_str):
    if not form_str:
        return 0
    score = 0
    cleaned = form_str.replace(",", "")
    for char in cleaned:
        if char == 'W': score += 3
        elif char == 'D': score += 1
        elif char == 'L': score -= 1
    return score

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
        
        home_points = home_stats.get("points", 0)
        away_points = away_stats.get("points", 0)
        home_position = home_stats.get("position", 10)
        away_position = away_stats.get("position", 10)
        
        home_form_pts = parse_form_score(home_stats.get("form", ""))
        away_form_pts = parse_form_score(away_stats.get("form", ""))
        
        # Expected Goals (xG) Calculation
        home_played = max(home_stats.get("playedGames", 1), 1)
        away_played = max(away_stats.get("playedGames", 1), 1)
        
        home_gf = home_stats.get("goalsFor", 0) / home_played
        home_ga = home_stats.get("goalsAgainst", 0) / home_played
        away_gf = away_stats.get("goalsFor", 0) / away_played
        away_ga = away_stats.get("goalsAgainst", 0) / away_played
        
        exp_home_goals = round((home_gf + away_ga) / 2, 2)
        exp_away_goals = round((away_gf + home_ga) / 2, 2)
        total_exp_goals = round(exp_home_goals + exp_away_goals, 2)
        
        # Additional Markets: BTTS & Over 2.5
        btts = "Yes" if (exp_home_goals >= 0.85 and exp_away_goals >= 0.85) else "No"
        over_2_5 = "Yes" if total_exp_goals >= 2.5 else "Under 2.5"
        over_1_5 = "Yes" if total_exp_goals >= 1.6 else "Risky / Under"
        
        # Head-to-Head Data
        h2h = fetch_h2h_data(match_id)
        home_h2h_wins = h2h.get("homeTeam", {}).get("wins", 0) if h2h else 0
        away_h2h_wins = h2h.get("awayTeam", {}).get("wins", 0) if h2h else 0
        
        # Weighted Scoring Model
        home_score = (20 - home_position) * 1.2 + (home_points * 0.3) + (home_form_pts * 0.8) + (home_h2h_wins * 0.5) + 3.0
        away_score = (20 - away_position) * 1.2 + (away_points * 0.3) + (away_form_pts * 0.8) + (away_h2h_wins * 0.5)
        
        score_diff = home_score - away_score
        
        if score_diff > 4.5:
            predicted_winner = home_team
            confidence = "High"
            double_chance = f"{home_team} or Draw (1X)"
        elif score_diff < -4.5:
            predicted_winner = away_team
            confidence = "High"
            double_chance = f"{away_team} or Draw (X2)"
        elif 2.0 <= score_diff <= 4.5:
            predicted_winner = home_team
            confidence = "Medium"
            double_chance = f"{home_team} or Draw (1X)"
        elif -4.5 <= score_diff <= -2.0:
            predicted_winner = away_team
            confidence = "Medium"
            double_chance = f"{away_team} or Draw (X2)"
        else:
            predicted_winner = "Draw / Closely Contested"
            confidence = "Low"
            double_chance = "Tight Match / Draw (12 or X)"
            
        predictions.append({
            "date": match_date_only,
            "kickoff": kickoff_display,
            "league": LEAGUES.get(comp_code, comp_code),
            "match": f"{home_team} vs {away_team}",
            "prediction": predicted_winner,
            "confidence": confidence,
            "double_chance": double_chance,
            "btts": btts,
            "over_2_5": over_2_5,
            "over_1_5": over_1_5,
            "exp_goals": f"{exp_home_goals} - {exp_away_goals} (Tot: {total_exp_goals})"
        })
        
    return predictions

def update_tracker_and_evaluate(new_predictions):
    if os.path.exists(TRACKER_FILE):
        df = pd.read_csv(TRACKER_FILE)
    else:
        df = pd.DataFrame(columns=["Date", "League", "Match", "Prediction", "Confidence", "Actual", "Status"])
    
    pending_rows = df[df["Status"] == "PENDING"]
    if not pending_rows.empty:
        oldest_date = pending_rows["Date"].min()
        today_str = datetime.now().strftime("%Y-%m-%d")
        url = f"{BASE_URL}/matches?dateFrom={oldest_date}&dateTo={today_str}"
        response = requests.get(url, headers=HEADERS)
        
        if response.status_code == 200:
            finished_matches = response.json().get("matches", [])
            for idx, row in pending_rows.iterrows():
                match_desc = row["Match"]
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
                                
                            df.loc[idx, "Actual"] = f"{h_name} {h_goals}-{a_goals} {a_name}"
                            df.loc[idx, "Status"] = "WON" if row["Prediction"] == actual_winner else "LOST"
                            break

    new_rows = []
    for p in new_predictions:
        existing = df[(df["Date"] == p["date"]) & (df["Match"] == p["match"])]
        if existing.empty:
            new_rows.append({
                "Date": p["date"],
                "League": p["league"],
                "Match": p["match"],
                "Prediction": p["prediction"],
                "Confidence": p["confidence"],
                "Actual": "Pending",
                "Status": "PENDING"
            })
            
    if new_rows:
        new_df = pd.DataFrame(new_rows)
        df = pd.concat([df, new_df], ignore_index=True)
        
    df.to_csv(TRACKER_FILE, index=False)
    return df

def calculate_tier_analytics(tracker_df):
    """Calculates granular success rates broken down by confidence tier."""
    completed = tracker_df[tracker_df["Status"].isin(["WON", "LOST"])]
    if completed.empty:
        return "No completed matches evaluated yet."
    
    analytics_summary = ""
    for tier in ["High", "Medium", "Low"]:
        tier_df = completed[completed["Confidence"] == tier]
        total = len(tier_df)
        won = len(tier_df[tier_df["Status"] == "WON"])
        rate = (won / total * 100) if total > 0 else 0.0
        analytics_summary += f"• <b>{tier} Confidence:</b> {won}/{total} Won ({rate:.1f}%)\n"
    return analytics_summary

def send_telegram_alert(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials missing. Skipping mobile alert.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            print("Telegram alert sent successfully!")
        else:
            print(f"Failed to send Telegram alert: {response.text}")
    except Exception as e:
        print(f"Telegram error: {e}")

def send_email(predictions, tracker_df):
    if not EMAIL_SENDER or not EMAIL_PASSWORD or not EMAIL_RECEIVER:
        print("Email credentials missing.")
        return

    completed = tracker_df[tracker_df["Status"].isin(["WON", "LOST"])]
    total_completed = len(completed)
    total_won = len(completed[completed["Status"] == "WON"])
    overall_accuracy = (total_won / total_completed * 100) if total_completed > 0 else 0.0

    tier_breakdown = calculate_tier_analytics(tracker_df)

    html_content = "<h2>⚽ Pro Football Analytics & Market Report</h2>"
    html_content += f"<p>Report generated on <b>{datetime.now().strftime('%Y-%m-%d')}</b> covering all 12 leagues.</p>"
    
    # Overall & Tier Analytics Banner
    html_content += f"<div style='background: #e9ecef; padding: 10px; border-radius: 5px; margin-bottom: 15px;'>"
    html_content += f"<b>Overall Model Track Record:</b> {total_won}/{total_completed} Completed ({overall_accuracy:.1f}% Success)<br>"
    html_content += f"<b>Performance by Tier:</b><br>{tier_breakdown.replace(chr(10), '<br>')}"
    html_content += "</div>"

    if not predictions:
        html_content += "<p><b>No upcoming matches found in the next 4 days across monitored leagues.</b></p>"
        telegram_msg = "⚽ <b>Football Predictions</b>\nNo upcoming matches found in the 4-day window."
    else:
        html_content += "<h3>📅 Full Upcoming Fixtures Table</h3>"
        html_content += "<table border='1' cellspacing='0' cellpadding='5' style='border-collapse: collapse; width: 100%; font-family: sans-serif; font-size: 11px;'>"
        html_content += "<tr style='background-color: #f8f9fa;'><th>Kickoff</th><th>League</th><th>Matchup</th><th>Pick</th><th>Conf</th><th>BTTS</th><th>O 2.5</th></tr>"
        
        high_list, med_list, btts_list, over25_list = [], [], [], []

        for p in predictions:
            html_content += f"<tr><td>{p['kickoff']}</td><td>{p['league']}</td><td>{p['match']}</td><td><b>{p['prediction']}</b></td><td>{p['confidence']}</td><td>{p['btts']}</td><td>{p['over_2_5']}</td></tr>"
            
            item_desc = f"<b>{p['match']}</b> ({p['league']}) ➔ Kickoff: {p['kickoff']} | Pick: <b>{p['prediction']}</b> | xG: {p['exp_goals']}"
            if p['confidence'] == 'High': high_list.append(item_desc)
            elif p['confidence'] == 'Medium': med_list.append(item_desc)
            
            if p['btts'] == 'Yes': btts_list.append(f"<b>{p['match']}</b> ({p['kickoff']})")
            if p['over_2_5'] == 'Yes': over25_list.append(f"<b>{p['match']}</b> ({p['kickoff']})")

        html_content += "</table>"

        html_content += "<h3 style='color: #2b8a3e;'>🔥 High Probability Shortlist</h3><ul>" + ("".join([f"<li>{x}</li>" for x in high_list]) if high_list else "<li>None today.</li>") + "</ul>"
        html_content += "<h3 style='color: #1971c2;'>⚡ Both Teams to Score (BTTS) Targets</h3><ul>" + ("".join([f"<li>{x}</li>" for x in btts_list]) if btts_list else "<li>None today.</li>") + "</ul>"
        html_content += "<h3>⚽ Over 2.5 Goals Targets</h3><ul>" + ("".join([f"<li>{x}</li>" for x in over25_list]) if over25_list else "<li>None today.</li>") + "</ul>"

        # Construct Telegram Push Alert
        telegram_msg = f"⚽ <b>Football Predictions Report</b>\n\n"
        telegram_msg += f"📊 <b>Accuracy:</b> {overall_accuracy:.1f}%\n"
        if high_list:
            telegram_msg += f"\n🔥 <b>High Confidence Picks:</b>\n" + "\n".join([f"• {item}" for item in high_list[:5]])
        if btts_list:
            telegram_msg += f"\n\n⚡ <b>BTTS Targets:</b>\n" + "\n".join([f"• {item}" for item in btts_list[:5]])

    # Send Email
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Pro Football Predictions & Analytics - {datetime.now().strftime('%Y-%m-%d')}"
    msg["From"] = EMAIL_SENDER
    msg["To"] = EMAIL_RECEIVER
    msg.attach(MIMEText(html_content, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())
        print("Email report sent successfully!")
    except Exception as e:
        print(f"Failed to send email: {e}")

    # Send Telegram Notification
    send_telegram_alert(telegram_msg)

if __name__ == "__main__":
    print("Running advanced pro football analytics pipeline...")
    matches = fetch_upcoming_matches()
    predictions = analyze_and_predict(matches)
    tracker_df = update_tracker_and_evaluate(predictions)
    send_email(predictions, tracker_df)
    print("Execution complete.")
