import logging
import asyncio
import random
import discord
from discord import app_commands
from discord.ext import commands

import config
import database
from firebase_sync import FirebaseClient
from solver import InstaLingSolver
from scheduler import SessionScheduler

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("InstaLingBot")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

firebase_client = FirebaseClient()
solver = InstaLingSolver(firebase_client, headless=config.HEADLESS)
scheduler = SessionScheduler(bot, solver, firebase_client)

@bot.event
async def on_ready():
    logger.info(f"🤖 Bot zalogowany jako {bot.user} (ID: {bot.user.id})")
    
    # Inicjalizacja bazy słówek
    await firebase_client.fetch_database()
    
    # Usunięcie podwójnych komend (czyścimy kopie z serwera, zostawiamy 1 zestaw globalny)
    try:
        for guild in bot.guilds:
            try:
                bot.tree.clear_commands(guild=guild)
                await bot.tree.sync(guild=guild)
            except Exception:
                pass

        synced = await bot.tree.sync()
        logger.info(f"✨ Usunięto duplikaty! Zsynchronizowano {len(synced)} pojedynczych komend.")
    except Exception as e:
        logger.error(f"Błąd synchronizacji komend: {e}")

    # Uruchomienie harmonogramu
    scheduler.start()

@bot.command(name="sync")
async def sync_cmd(ctx):
    """Ręczne wymuszenie synchronizacji komend wpisując !sync na czacie"""
    if ctx.author.guild_permissions.administrator:
        msg = await ctx.send("⏳ Usuwam duplikaty i odświeżam komendy...")
        for guild in bot.guilds:
            try:
                bot.tree.clear_commands(guild=guild)
                await bot.tree.sync(guild=guild)
            except Exception:
                pass
        synced = await bot.tree.sync()
        await msg.edit(content=f"✅ Zsynchronizowano {len(synced)} pojedynczych komend (duplikaty usunięte)!")
    else:
        await ctx.send("❌ Tylko administrator serwera może użyć !sync.")

# ===== SPRAWDZANIE UPRAWNIEŃ RANGI =====

async def has_required_role(interaction: discord.Interaction) -> bool:
    """Sprawdza czy użytkownik posiada wymaganą rangę (lub jest administratorem)"""
    # 1. Administratorzy serwera mają zawsze pełny dostęp
    if interaction.guild and isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.administrator:
        return True

    # 2. Jeśli ograniczenie rangi jest wyłączone w configu (pusta nazwa i ID 0)
    if not config.REQUIRED_ROLE_NAME and config.REQUIRED_ROLE_ID == 0:
        return True

    # 3. Jeśli komenda wywołana na serwerze
    if isinstance(interaction.user, discord.Member):
        roles = interaction.user.roles
        if config.REQUIRED_ROLE_ID != 0 and any(r.id == config.REQUIRED_ROLE_ID for r in roles):
            return True
        if config.REQUIRED_ROLE_NAME and any(r.name.lower() == config.REQUIRED_ROLE_NAME.lower() for r in roles):
            return True

    # 4. Jeśli komenda wywołana w wiadomości prywatnej (DM)
    if interaction.guild is None:
        for guild in bot.guilds:
            member = guild.get_member(interaction.user.id)
            if member:
                if member.guild_permissions.administrator:
                    return True
                if config.REQUIRED_ROLE_ID != 0 and any(r.id == config.REQUIRED_ROLE_ID for r in member.roles):
                    return True
                if config.REQUIRED_ROLE_NAME and any(r.name.lower() == config.REQUIRED_ROLE_NAME.lower() for r in member.roles):
                    return True

    # Brak uprawnień -> wyślij informację
    role_info = f"**{config.REQUIRED_ROLE_NAME}**" if config.REQUIRED_ROLE_NAME else f"o ID: `{config.REQUIRED_ROLE_ID}`"
    embed = discord.Embed(
        title="⛔ Brak Wymaganej Rangi!",
        description=(
            f"Nie masz uprawnień do korzystania z bota InstaLing.\n\n"
            f"👑 Wymagana ranga: {role_info}\n\n"
            f"Skontaktuj się z administratorem serwera, aby otrzymać dostęp."
        ),
        color=discord.Color.red()
    )
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=True)
    return False

@bot.tree.interaction_check
async def interaction_check(interaction: discord.Interaction) -> bool:
    return await has_required_role(interaction)

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        return
    logger.error(f"Błąd komendy: {error}", exc_info=True)

# ===== KOMENDY SLASH =====

@bot.tree.command(name="dodaj_konto", description="Dodaj konto InstaLing do bota (dane są prywatne)")
@app_commands.describe(
    login="Email lub login do InstaLinga",
    haslo="Hasło do konta InstaLing",
    jezyk="Wybierz język (auto, de, en)"
)
@app_commands.choices(jezyk=[
    app_commands.Choice(name="Automatyczny (wykryj sam)", value="auto"),
    app_commands.Choice(name="Niemiecki (DE)", value="de"),
    app_commands.Choice(name="Angielski (EN)", value="en")
])
async def dodaj_konto(interaction: discord.Interaction, login: str, haslo: str, jezyk: app_commands.Choice[str] = None):
    # Ephemeral = tylko osoba wywołująca komendę widzi odpowiedź (hasło jest bezpieczne!)
    await interaction.response.defer(ephemeral=True)
    
    lang_val = jezyk.value if jezyk else "auto"

    database.add_or_update_account(
        discord_user_id=interaction.user.id,
        login=login,
        password=haslo,
        language=lang_val,
        auto_daily=1
    )

    embed = discord.Embed(
        title="✅ Konto zapisane pomyślnie!",
        description=f"Konto `{login}` zostało dodane. Bot będzie wykonywał dla niego sesje automatycznie codziennie rano.",
        color=discord.Color.green()
    )
    embed.add_field(name="🎯 Język", value=f"`{lang_val.upper()}`", inline=True)
    embed.set_footer(text="Możesz teraz uruchomić sesję komendą /sesja")

    await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name="konta", description="Wyświetl listę swoich zapisanych kont InstaLing")
async def konta(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    accounts = database.get_user_accounts(interaction.user.id)

    if not accounts:
        await interaction.followup.send("ℹ️ Nie masz jeszcze żadnych zapisanych kont. Użyj `/dodaj_konto` aby dodać pierwsze!", ephemeral=True)
        return

    embed = discord.Embed(
        title=f"📋 Twoje konta InstaLing ({len(accounts)})",
        description="Wszystkie Twoje konta są aktywne i wykonują się codziennie rano.",
        color=discord.Color.blue()
    )

    for i, acc in enumerate(accounts, start=1):
        last_str = acc["last_run"] or "Brak danych"
        embed.add_field(
            name=f"{i}. Login: `{acc['login']}`",
            value=f"🎯 Język: `{acc['language'].upper()}`\n🕒 Ostatnia sesja: `{last_str}`",
            inline=False
        )

    await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name="usun_konto", description="Usuń konto InstaLing z bazy")
@app_commands.describe(login="Login konta do usunięcia")
async def usun_konto(interaction: discord.Interaction, login: str):
    await interaction.response.defer(ephemeral=True)
    success = database.delete_account(interaction.user.id, login)

    if success:
        await interaction.followup.send(f"✅ Konto `{login}` zostało usunięte z bota.", ephemeral=True)
    else:
        await interaction.followup.send(f"❌ Nie znaleziono konta `{login}` przypisanego do Twojego profilu.", ephemeral=True)

@bot.tree.command(name="sesja", description="Rozwiąż sesję InstaLing od ręki (dla jednego konta lub wszystkich)")
@app_commands.describe(
    konto="Login konta (zostaw puste, aby zrobić WSZYSTKIE Twoje konta)",
    wszystkie="Czy zrobić sesję dla WSZYSTKICH Twoich kont?"
)
@app_commands.choices(wszystkie=[
    app_commands.Choice(name="Tak (wszystkie moje konta)", value="tak"),
    app_commands.Choice(name="Nie (tylko wskazane konto)", value="nie")
])
async def sesja(interaction: discord.Interaction, konto: str = None, wszystkie: app_commands.Choice[str] = None):
    await interaction.response.defer()

    accounts = database.get_user_accounts(interaction.user.id)
    if not accounts:
        await interaction.followup.send("❌ Nie masz jeszcze żadnych zapisanych kont! Użyj `/dodaj_konto` aby dodać konto.", ephemeral=True)
        return

    # Ustalenie listy kont do wykonania
    do_all = (wszystkie and wszystkie.value == "tak") or (not konto and len(accounts) > 1)
    
    if not do_all and konto:
        targets = [a for a in accounts if a["login"].lower() == konto.lower().strip()]
        if not targets:
            await interaction.followup.send(f"❌ Nie znaleziono konta `{konto}` wśród Twoich zapisanych kont.", ephemeral=True)
            return
    elif not do_all and not konto:
        targets = [accounts[0]]
    else:
        targets = accounts

    total = len(targets)
    status_embed = discord.Embed(
        title="⏳ Rozwiązywanie sesji InstaLing...",
        description=f"Rozpoczynam sesje dla **{total}** kont...",
        color=discord.Color.gold()
    )
    msg = await interaction.followup.send(embed=status_embed)

    results = []
    remaining = list(targets)
    batch_idx = 1
    done_count = 0

    while remaining:
        # Losowa wielkość paczki od config.BATCH_SIZE_MIN do config.BATCH_SIZE_MAX (np. 2 do 5)
        # Jeśli zostało mniej kont, bierzemy wszystkie pozostałe
        batch_size = random.randint(config.BATCH_SIZE_MIN, config.BATCH_SIZE_MAX)
        cur_batch = remaining[:batch_size]
        remaining = remaining[batch_size:]

        logins_str = ", ".join([f"`{a['login']}`" for a in cur_batch])
        status_embed.description = (
            f"🔄 **Paczka #{batch_idx}** ({len(cur_batch)} kont naraz):\n"
            f"{logins_str}\n\n"
            f"📊 Postęp: **{done_count}/{total}** ukończonych\n"
            f"⏳ Proszę czekać..."
        )
        try:
            await msg.edit(embed=status_embed)
        except Exception:
            pass

        async def run_single_in_batch(acc, idx_in_batch):
            # Delikatne rozstrzelenie startu
            await asyncio.sleep(idx_in_batch * random.uniform(1.5, 4.0))
            r = await solver.solve_session(
                login=acc["login"],
                password=acc["password"],
                preferred_lang=acc.get("language", "auto")
            )
            if r["success"]:
                database.update_last_run(interaction.user.id, acc["login"], discord.utils.utcnow().strftime("%Y-%m-%d %H:%M:%S"))
            return (acc["login"], r)

        batch_tasks = [run_single_in_batch(acc, i) for i, acc in enumerate(cur_batch)]
        batch_res = await asyncio.gather(*batch_tasks)
        results.extend(batch_res)
        done_count += len(cur_batch)
        batch_idx += 1

        if remaining:
            pause_time = random.uniform(config.BATCH_PAUSE_MIN, config.BATCH_PAUSE_MAX)
            status_embed.description = (
                f"☕ **Paczka ukończona!**\n"
                f"Przerwa {pause_time:.1f}s przed kolejną paczką...\n"
                f"📊 Postęp: **{done_count}/{total}** ukończonych"
            )
            try:
                await msg.edit(embed=status_embed)
            except Exception:
                pass
            await asyncio.sleep(pause_time)

    # Podsumowanie
    success_count = sum(1 for _, r in results if r["success"])
    summary_embed = discord.Embed(
        title=f"📋 Raport z Sesji InstaLing ({success_count}/{total} zakończonych)",
        color=discord.Color.green() if success_count == total else discord.Color.orange()
    )

    for acc_login, res in results:
        lang_tag = res.get("language", "EN")
        if res.get("already_done"):
            val = "ℹ️ **Dzisiejsza sesja była już wykonana!**"
        elif res["success"]:
            val = f"✅ Poprawne: **{res['correct_words']}/{res['total_words']}** | Nowe: **+{res['learned_words']}** | Czas: `{res['time_seconds']}s`"
        else:
            val = f"❌ {res['message']}"
        summary_embed.add_field(name=f"👤 `{acc_login}` [{lang_tag}]", value=val, inline=False)

    summary_embed.set_footer(text="InstaLing AutoBot • dc.zinzelekk")
    await msg.edit(embed=summary_embed)

@bot.tree.command(name="baza", description="Sprawdź liczbę zapamiętanych słówek w bazie Firebase")
async def baza(interaction: discord.Interaction):
    await interaction.response.defer()
    await firebase_client.fetch_database()

    de_count = len(firebase_client.cache.get("de", {}))
    en_count = len(firebase_client.cache.get("en", {}))

    embed = discord.Embed(
        title="☁️ Globalna Baza Słówek InstaLing (Firebase)",
        color=discord.Color.purple()
    )
    embed.add_field(name="🇩🇪 Język Niemiecki (DE)", value=f"**{de_count}** słówek", inline=True)
    embed.add_field(name="🇬🇧 Język Angielski (EN)", value=f"**{en_count}** słówek", inline=True)
    embed.add_field(name="📈 Łącznie", value=f"**{de_count + en_count}** słówek", inline=True)
    embed.set_footer(text="Baza synchronizowana na bieżąco z userscriptem i botem")

    await interaction.followup.send(embed=embed)



if __name__ == "__main__":
    if not config.DISCORD_TOKEN or config.DISCORD_TOKEN == "WSTAW_TUTAJ_TOKEN_BOTA_DISCORD":
        print("="*60)
        print("❌ BŁĄD: Nie ustawiono tokena bota Discord!")
        print("Wklej swój token w pliku .env lub config.py w zmiennej DISCORD_TOKEN.")
        print("Token zdobędziesz na: https://discord.com/developers/applications")
        print("="*60)
    else:
        bot.run(config.DISCORD_TOKEN)
