import asyncio
import logging
from datetime import datetime
import discord
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

import config
import database
from solver import InstaLingSolver
from firebase_sync import FirebaseClient

logger = logging.getLogger("InstaLingBot.Scheduler")

class SessionScheduler:
    def __init__(self, bot: discord.Client, solver: InstaLingSolver, firebase: FirebaseClient):
        self.bot = bot
        self.solver = solver
        self.firebase = firebase
        self.scheduler = AsyncIOScheduler()

    def start(self):
        """Uruchamia harmonogram codziennego rozwiązywania sesji"""
        self.scheduler.add_job(
            self.run_daily_sessions,
            CronTrigger(hour=config.DEFAULT_AUTO_HOUR, minute=config.DEFAULT_AUTO_MINUTE),
            id="daily_instaling_job",
            replace_existing=True
        )
        self.scheduler.start()
        logger.info(f"⏰ Harmonogram aktywny! Codzienne sesje o {config.DEFAULT_AUTO_HOUR:02d}:{config.DEFAULT_AUTO_MINUTE:02d}.")

    async def run_daily_sessions(self):
        """Wykonuje sesje dla wszystkich kont z włączoną opcją auto_daily"""
        logger.info("🌅 Rozpoczynam automatyczne codzienne sesje...")
        accounts = database.get_all_auto_accounts()
        if not accounts:
            logger.info("Brak kont z włączonym harmonogramem.")
            return

        # Zaktualizuj bazę Firebase przed rozpoczęciem
        await self.firebase.fetch_database()

        for acc in accounts:
            user_id = acc["discord_user_id"]
            login = acc["login"]
            password = acc["password"]
            lang = acc.get("language", "auto")

            logger.info(f"⚙️ Auto-sesja dla użytkownika {user_id} (Konto: {login})...")
            try:
                res = await self.solver.solve_session(login, password, lang)
                database.update_last_run(user_id, login, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

                # Wyślij powiadomienie na DM do użytkownika na Discordzie
                try:
                    user = await self.bot.fetch_user(user_id)
                    if user:
                        embed = discord.Embed(
                            title="⏰ Automatyczna Sesja InstaLing",
                            color=discord.Color.green() if res["success"] else discord.Color.red(),
                            timestamp=datetime.now()
                        )
                        embed.add_field(name="👤 Konto", value=f"`{login}`", inline=True)
                        embed.add_field(name="🎯 Język", value=f"`{res['language']}`", inline=True)
                        embed.add_field(name="⏱️ Czas", value=f"`{res['time_seconds']}s`", inline=True)

                        if res["already_done"]:
                            embed.description = "ℹ️ Dzisiejsza sesja była już wcześniej zrobiona."
                        elif res["success"]:
                            embed.description = "✅ Twoja dzisiejsza sesja została pomyślnie wykonana!"
                            embed.add_field(name="📊 Rozwiązane", value=f"`{res['correct_words']}/{res['total_words']}`", inline=True)
                            embed.add_field(name="🧠 Nowe słówka", value=f"`+{res['learned_words']}`", inline=True)
                        else:
                            embed.description = f"❌ Nie udało się wykonać sesji: {res['message']}"

                        embed.set_footer(text="InstaLing AutoBot • dc.zinzelekk")
                        await user.send(embed=embed)
                except Exception as e:
                    logger.warning(f"Nie udało się wysłać wiadomości DM do {user_id}: {e}")

            except Exception as e:
                logger.error(f"Błąd auto-sesji dla {login}: {e}")

            # Krótka przerwa między kontami
            await asyncio.sleep(5)
