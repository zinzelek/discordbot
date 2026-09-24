import asyncio
import logging
import random
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
        """Wykonuje sesje dla wszystkich kont w losowych paczkach (np. 5, potem 3, potem 2, potem 4 itd.)"""
        logger.info("🌅 Rozpoczynam automatyczne codzienne sesje...")
        accounts = database.get_all_auto_accounts()
        if not accounts:
            logger.info("Brak kont z włączonym harmonogramem.")
            return

        # Zaktualizuj bazę Firebase przed rozpoczęciem
        await self.firebase.fetch_database()

        remaining_accounts = list(accounts)
        batch_idx = 1
        total_accounts = len(accounts)
        processed_count = 0

        logger.info(f"📋 Rozpoczynam wykonywanie sesji dla {total_accounts} kont w losowych paczkach...")

        while remaining_accounts:
            # Losowa wielkość paczki: od config.BATCH_SIZE_MIN do config.BATCH_SIZE_MAX (np. 2 do 5)
            batch_size = random.randint(config.BATCH_SIZE_MIN, config.BATCH_SIZE_MAX)
            current_batch = remaining_accounts[:batch_size]
            remaining_accounts = remaining_accounts[batch_size:]

            logins_str = ", ".join([a['login'] for a in current_batch])
            logger.info(f"📦 Paczka #{batch_idx} ({len(current_batch)} kont naraz: {logins_str}). Postęp: {processed_count}/{total_accounts}...")

            async def solve_and_notify(acc, index_in_batch):
                # Delikatne rozstrzelenie startu w sekundach, aby nie obciążać sieci w tej samej milisekundzie
                await asyncio.sleep(index_in_batch * random.uniform(2.0, 5.0))
                user_id = acc["discord_user_id"]
                login = acc["login"]
                password = acc["password"]
                lang = acc.get("language", "auto")

                logger.info(f"⚙️ Auto-sesja dla konta: {login} ({lang.upper()})...")
                try:
                    res = await self.solver.solve_session(login, password, lang)
                    database.update_last_run(user_id, login, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

                    # Wyślij powiadomienie na DM do użytkownika
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
                        logger.warning(f"Nie udało się wysłać DM do {user_id}: {e}")

                    return acc, res
                except Exception as e:
                    logger.error(f"Błąd auto-sesji dla {login}: {e}")
                    return acc, {"success": False, "message": str(e), "total_words": 0, "correct_words": 0, "learned_words": 0}

            # Wykonaj wszystkie konta z obecnej paczki równolegle
            batch_tasks = [solve_and_notify(acc, i) for i, acc in enumerate(current_batch)]
            await asyncio.gather(*batch_tasks)

            processed_count += len(current_batch)
            batch_idx += 1

            # Przerwa między paczkami (jeśli zostały jeszcze jakieś konta do zrobienia)
            if remaining_accounts:
                pause_time = random.uniform(config.BATCH_PAUSE_MIN, config.BATCH_PAUSE_MAX)
                logger.info(f"☕ Paczka ukończona. Przerwa {pause_time:.1f}s przed kolejną paczką...")
                await asyncio.sleep(pause_time)

        logger.info(f"🎉 Wszystkie {total_accounts} kont z harmonogramu zostały ukończone!")
