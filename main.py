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
    """Fetches matches across all 12 free leagues from today up to the next 4 days."""
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
        
        # Parse Kickoff Time
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
        
        # Head-to-Head Data
        h2h = fetch_h2h_data(match_id)
        home_h2h_wins = h2h.get("homeTeam", {}).get("wins", 0) if h2h else 0
        away_h2h_wins = h2h.get("awayTeam", {}).get("wins", 0) if h2h else 0
        
        # Weighted Scoring Model
        home_score = (20 - home_position) * 1.2 + (home_points * 0.3) + (home_form_pts * 0.8) + (home_h2h_wins * 0.5) + 3.0
        away_score = (20 - away_position) * 1.2 + (away_points * 0.3) + (away_form_pts * 0.8) + (away_h2h_wins * 0.5)
        
        score_diff = home_score - away_score
        
        # Precise Categorization
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
            
        over_1_5 = "Yes" if total_exp_goals >= 1.6 else "Risky / Under"
            
        predictions.append({
            "date": match_date_only,
            "kickoff": kickoff_display,
            "league": LEAGUES.get(comp_code, comp_code),
            "match": f"{home_team} vs {away_team}",
            "prediction": predicted_winner,
            "confidence": confidence,
            "double_chance": double_chance,
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

def send_email(predictions, tracker_df):
    if not EMAIL_SENDER or not EMAIL_PASSWORD or not EMAIL_RECEIVER:
        print("Email credentials missing.")
        return

    completed = tracker_df[tracker_df["Status"].isin(["WON", "LOST"])]
    total_completed = len(completed)
    total_won = len(completed[completed["Status"] == "WON"])
    accuracy_pct = (total_won / total_completed * 100) if total_completed > 0 else 0.0

    html_content = "<h2>⚽ Global 12-League Football Predictions & Shortlists</h2>"
    html_content += f"<p>Report generated on <b>{datetime.now().strftime('%Y-%m-%d')}</b> covering all 12 free competitions for the next 4 days.</p>"
    
    html_content += f"<div style='background: #e9ecef; padding: 10px; border-radius: 5px; margin-bottom: 15px;'>"
    html_content += f"<b>Model Track Record:</b> {total_won} Won / {total_completed} Completed ({accuracy_pct:.1f}% Success Rate)"
    html_content += "</div>"

    if not predictions:
        html_content += "<p><b>No upcoming matches found in the next 4 days across the 12 monitored leagues.</b></p>"
    else:
        html_content += "<h3>📅 Full Upcoming Fixtures Table</h3>"
        html_content += "<table border='1' cellspacing='0' cellpadding='6' style='border-collapse: collapse; width: 100%; font-family: sans-serif; font-size: 12px;'>"
        html_content += "<tr style='background-color: #f8f9fa;'><th>Kickoff</th><th>League</th><th>Matchup</th><th>Prediction</th><th>Confidence</th><th>Double Chance</th><th>Over 1.5</th></tr>"
        
        high_list, med_list, low_list, over_15_list, dc_list = [], [], [], [], []

        for p in predictions:
            html_content += f"<tr><td>{p['kickoff']}</td><td>{p['league']}</td><td>{p['match']}</td><td><b>{p['prediction']}</b></td><td>{p['confidence']}</td><td>{p['double_chance']}</td><td>{p['over_1_5']}</td></tr>"
            
            item_desc = f"<b>{p['match']}</b> ({p['league']}) ➔ <b>Kickoff: {p['kickoff']}</b> | Pick: <b>{p['prediction']}</b> | xG: {p['exp_goals']}"
            
            if p['confidence'] == 'High':
                high_list.append(item_desc)
            elif p['confidence'] == 'Medium':
                med_list.append(item_desc)
            else:
                low_list.append(item_desc)
                
            if p['over_1_5'] == 'Yes':
                over_15_list.append(f"<b>{p['match']}</b> ({p['kickoff']}) ➔ xG: {p['exp_goals']}")
                
            dc_list.append(f"<b>{p['match']}</b> ({p['kickoff']}) ➔ {p['double_chance']}")

        html_content += "</table>"

        html_content += "<h3 style='color: #2b8a3e;'>🔥 High Probability Shortlist</h3><ul>" + ("".join([f"<li>{x}</li>" for x in high_list]) if high_list else "<li>No high probability matches in this window.</li>") + "</ul>"
        html_content += "<h3 style='color: #1971c2;'>⚡ Medium Probability Shortlist</h3><ul>" + ("".join([f"<li>{x}</li>" for x in med_list]) if med_list else "<li>No medium probability matches in this window.</li>") + "</ul>"
        html_content += "<h3 style='color: #e67700;'>⚠️ Low Probability / Tight Matches Shortlist</h3><ul>" + ("".join([f"<li>{x}</li>" for x in low_list]) if low_list else "<li>No low probability/tight matches in this window.</li>") + "</ul>"
        html_content += "<h3>🛡️ Double Chance Recommendations</h3><ul>" + ("".join([f"<li>{x}</li>" for x in dc_list]) if dc_list else "<li>None available.</li>") + "</ul>"
        html_content += "<h3>⚽ Matches Expected Over 1.5 Goals</h3><ul>" + ("".join([f"<li>{x}</li>" for x in over_15_list]) if over_15_list else "<li>None expected over 1.5 goals in this window.</li>") + "</ul>"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"12-League Football Predictions & Shortlists - {datetime.now().strftime('%Y-%m-%d')}"
    msg["From"] = EMAIL_SENDER
    msg["To"] = EMAIL_RECEIVER
    msg.attach(MIMEText(html_content, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())
        print("Predictions email sent successfully!")
    except Exception as e:
        print(f"Failed to send email: {e}")

if __name__ == "__main__":
    print("Running 12-league fixture and prediction engine...")
    matches = fetch_upcoming_matches()
    predictions = analyze_and_predict(matches)
    tracker_df = update_tracker_and_evaluate(predictions)
    send_email(predictions, tracker_df)
    print("Execution complete.")
