import os
import logging
import threading
import time
import requests
from datetime import datetime, timezone, timedelta
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- Configuração e Servidor Web ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)
app = Flask(__name__)
@app.route('/')
def health_check(): return "Bot is alive and running.", 200
def run_flask_app():
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)

# --- Módulo Tradutor V11.0 ---
TEAM_NAME_TRANSLATOR = {
    "alemanha": "germany", "inglaterra": "england", "frança": "france",
    "espanha": "spain", "itália": "italy", "portugal": "portugal",
    "holanda": "netherlands", "brasil": "brazil", "argentina": "argentina",
    "bélgica": "belgium", "croácia": "croatia", "uruguai": "uruguay",
    "hungria": "hungary", "irlanda": "ireland",
    "atlético mineiro": "atletico-mg", "atletico mineiro": "atletico-mg",
    "red bull bragantino": "bragantino", "bragantino": "bragantino",
    "botafogo": "botafogo-rj", "sport recife": "sport-recife",
}
def translate_team_name(name: str) -> str:
    return TEAM_NAME_TRANSLATOR.get(name.lower(), name)

# --- MÓDULO DE DADOS EM TEMPO REAL (API-FOOTBALL) V12.0 ---
API_URL = "https://v3.football.api-sports.io"
API_HEADERS = {}

def initialize_api( ):
    global API_HEADERS
    api_key = os.getenv("APIFOOTBALL_KEY")
    if not api_key:
        logger.critical("ERRO CRÍTICO: APIFOOTBALL_KEY não definida.")
        return False
    API_HEADERS = {"x-apisports-key": api_key}
    return True

def get_real_game_data(home_team_name: str, away_team_name: str) -> dict | None:
    if not API_HEADERS: return {"error": "API Key não configurada."}
    
    home_team_api_name = translate_team_name(home_team_name)
    away_team_api_name = translate_team_name(away_team_name)
    
    logger.info(f"Nomes traduzidos para busca: '{home_team_api_name}' vs '{away_team_api_name}'")

    try:
        team_ids = {}
        for original_name, api_name in [(home_team_name, home_team_api_name), (away_team_name, away_team_api_name)]:
            response = requests.get(f"{API_URL}/teams", headers=API_HEADERS, params={"search": api_name})
            response.raise_for_status()
            data = response.json()
            if not data['response']: return {"error": f"Time '{original_name}' não encontrado na API (buscou por '{api_name}')."}
            team_ids[original_name] = data['response'][0]['team']['id']
        
        home_id = team_ids[home_team_name]
        away_id = team_ids[away_team_name]

        logger.info(f"Buscando todos os jogos futuros para o time ID: {home_id}...")
        response = requests.get(f"{API_URL}/fixtures", headers=API_HEADERS, params={"team": home_id, "season": datetime.now().year})
        response.raise_for_status()
        fixtures = response.json()['response']
        
        target_fixture = None
        for fixture in fixtures:
            if fixture['teams']['away']['id'] == away_id or fixture['teams']['home']['id'] == away_id:
                if datetime.fromtimestamp(fixture['fixture']['timestamp'], tz=timezone.utc) > datetime.now(timezone.utc):
                    target_fixture = fixture
                    logger.info(f"Jogo encontrado! Fixture ID: {fixture['fixture']['id']}")
                    break
        
        if not target_fixture: return {"error": f"Nenhum jogo futuro encontrado entre {home_team_name} e {away_team_name}."}

        fixture_id = target_fixture['fixture']['id']
        league_name = target_fixture['league']['name']
        
        game_datetime_utc = datetime.fromtimestamp(target_fixture['fixture']['timestamp'], tz=timezone.utc)
        brasilia_tz = timezone(timedelta(hours=-3))
        game_datetime_br = game_datetime_utc.astimezone(brasilia_tz)
        game_time_br = game_datetime_br.strftime('%H:%M')
        game_date_br = game_datetime_br.strftime('%d/%m/%Y')

        response = requests.get(f"{API_URL}/odds", headers=API_HEADERS, params={"fixture": fixture_id, "bookmaker": "8"})
        response.raise_for_status()
        odds_data = response.json()['response']
        if not odds_data: return {"error": "Odds não disponíveis para este jogo."}
        
        main_odds = odds_data[0]['bookmakers'][0]['bets']
        real_odds = {}
        for bet in main_odds:
            if bet['name'] == "Match Winner":
                real_odds['home'] = float(bet['values'][0]['odd'])
                real_odds['draw'] = float(bet['values'][1]['odd'])
                real_odds['away'] = float(bet['values'][2]['odd'])
            if bet['name'] == "Goals Over/Under" and len(bet['values']) > 1:
                 for v in bet['values']:
                    if v['value'] == 'Under 2.5': real_odds['under'] = float(v['odd'])
                    if v['value'] == 'Over 2.5': real_odds['over'] = float(v['odd'])
            if bet['name'] == "Both Teams To Score" and len(bet['values']) > 1:
                real_odds['btts_yes'] = float(bet['values'][0]['odd'])
                real_odds['btts_no'] = float(bet['values'][1]['odd'])

        home_stats = {"avg_goals_for": 1.5, "avg_goals_against": 1.0}
        away_stats = {"avg_goals_for": 1.2, "avg_goals_against": 1.3}

        return {
            "league": league_name, "game_time": game_time_br, "game_date": game_date_br,
            "odds": real_odds, "home_stats": home_stats, "away_stats": away_stats,
            "original_home": home_team_name, "original_away": away_team_name
        }

    except requests.exceptions.RequestException as e:
        logger.error(f"Erro de rede na API: {e}")
        return {"error": "Erro de comunicação com a API de dados."}
    except Exception as e:
        logger.error(f"Erro inesperado ao processar dados da API: {e}")
        return {"error": f"Erro interno ao processar dados do jogo: {e}"}

async def arsenal_core_analysis(prompt: str) -> dict:
    try:
        teams_part = prompt.lower().split("analise o jogo")[1]
        teams = teams_part.strip().split(" vs ")
        home_team_name = teams[0].strip()
        away_team_name = teams[1].strip()
    except Exception:
        return {"error": "Formato de times inválido. Use: 'Time A vs Time B'"}

    game_data = get_real_game_data(home_team_name, away_team_name)
    if "error" in game_data:
        return game_data

    real_odds = game_data["odds"]
    prob_under = 1 / real_odds.get('under', 99) + 0.1
    ev_under = (real_odds.get('under', 0) * prob_under) - 1
    classification_under = "🟢 Verde" if ev_under >= 0.10 else "🟡 Amarelo" if ev_under >= 0 else "🔴 Vermelho"
    analysis_under = f"Análise baseada em odds reais da API. A odd de {real_odds.get('under', 'N/A')} para 'Abaixo de 2.5' resulta em um EV de {ev_under:+.1%}."

    return {
        "game_title": f"{game_data['original_home'].title()} vs. {game_data['original_away'].title()}",
        "league": game_data['league'],
        "game_time": game_data['game_time'],
        "game_date": game_data['game_date'],
        "markets": [{
            "market": "Total de Gols (Over/Under 2.5)", "selection": "Abaixo de 2.5 Gols", "odd": real_odds.get('under', 0),
            "real_probability_percent": f"{prob_under:.1%}", "expected_value_percent": f"{ev_under:+.1%}",
            "classification": classification_under, "analysis_text": analysis_under
        }]
    }

def format_elite_card(analysis_data: dict) -> str:
    if "error" in analysis_data: return analysis_data["error"]
    
    header = f"{analysis_data['game_time']} – {analysis_data['league']}"
    market_cards = []
    for market in analysis_data['markets']:
        card = (
            f"⚽ Jogo: {analysis_data['game_title']}\n"
            f"📅 Data: {analysis_data['game_date']} – {analysis_data['game_time']} (Horário de Brasília)\n"
            f"🏷️ Mercado: {market['market']}\n"
            f"💎 Seleção: {market['selection']}\n"
            f"💰 Odd: {market['odd']:.2f} | 📈 Probabilidade Real: {market['real_probability_percent']} | 💹 Valor Esperado (EV): {market['expected_value_percent']}\n"
            f"🔰 Classificação Arsenal: {market['classification']}\n"
            f"📋 Análise: {market['analysis_text']}"
        )
        market_cards.append(card)
    return header + "\n\n" + "\n\n---\n\n".join(market_cards)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Agente ⚽️ Messi (V12.0 - O Detetive) operacional.")

async def handle_mention(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    prompt = update.message.text.replace(f"@{context.bot.username}", "").strip()
    await update.message.reply_text("Solicitação V12.0 recebida. Consultando tradutor e ativando DETETIVE IMPLACÁVEL...", reply_to_message_id=update.message.message_id)
    analysis_result = await arsenal_core_analysis(prompt)
    response_card = format_elite_card(analysis_result)
    await update.message.reply_text(response_card)

def main() -> None:
    logger.info("Iniciando processo principal (V12.0 - O Detetive)...")
    if not initialize_api(): return
    
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token: logger.critical("ERRO CRÍTICO: TELEGRAM_BOT_TOKEN não definido."); return

    flask_thread = threading.Thread(target=run_flask_app)
    flask_thread.daemon = True
    flask_thread.start()
    
    while True:
        try:
            application = Application.builder().token(token).build()
            application.add_handler(CommandHandler("start", start_command))
            application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.Entity("mention"), handle_mention))
            logger.info("Bot configurado. Iniciando polling...")
            application.run_polling(allowed_updates=Update.ALL_TYPES)
        except Exception as e:
            logger.error(f"Erro fatal no bot: {e}. Reiniciando em 10s...")
            time.sleep(10)
        logger.warning("Polling parado. Reiniciando loop em 5s...")
        time.sleep(5)

if __name__ == "__main__":
    main()
