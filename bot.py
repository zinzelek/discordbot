import logging
import asyncio
import random
import time
from datetime import datetime, timedelta
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
    # Jeśli bot działa w wiadomości prywatnej (DM)
    if interaction.guild is None:
        return True

    # Jeśli w configu nie podano rangi, bot jest otwarty dla każdego
    req_name = config.REQUIRED_ROLE_NAME.strip().lower()
    req_id = config.REQUIRED_ROLE_ID

    if not req_name and req_id == 0:
        return True

    user = interaction.user
    if not isinstance(user, discord.Member):
        return True

    # Administratorzy serwera mają zawsze pełny dostęp
    if user.guild_permissions.administrator:
        return True

    # Sprawdzenie po ID roli
    if req_id != 0:
        for role in user.roles:
            if role.id == req_id:
                return True

    # Sprawdzenie po nazwie roli
    if req_name:
        for role in user.roles:
            if role.name.lower() == req_name:
                return True

    return False

@bot.tree.interaction_check
async def interaction_check(interaction: discord.Interaction) -> bool:
    allowed = await has_required_role(interaction)
    if not allowed:
        req = config.REQUIRED_ROLE_NAME if config.REQUIRED_ROLE_NAME else f"ID: {config.REQUIRED_ROLE_ID}"
        embed = discord.Embed(
            title="⛔ Brak uprawnień!",
            description=f"Ta komenda jest dostępna tylko dla osób z rangą **`{req}`** lub Administratorów.",
            color=discord.Color.red()
        )
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
        return False
    return True

# ===== KOMENDY DISCORD =====

@bot.tree.command(name="dodaj_konto", description="Dodaj konto InstaLing do bota")
@app_commands.describe(
    login="Email lub login do InstaLinga",
    haslo="Hasło do konta InstaLing",
    jezyk="Wybierz język nauki (domyślnie: auto)",
    waznosc="Wybierz czas ważności konta (np. tydzień, miesiąc, na zawsze)"
)
@app_commands.choices(jezyk=[
    app_commands.Choice(name="Automatyczny (wykryj sam)", value="auto"),
    app_commands.Choice(name="Niemiecki (DE)", value="de"),
    app_commands.Choice(name="Angielski (EN)", value="en")
])
@app_commands.choices(waznosc=[
    app_commands.Choice(name="Tydzień (7 dni)", value="7d"),
    app_commands.Choice(name="Miesiąc (30 dni)", value="30d"),
    app_commands.Choice(name="Na zawsze (bez limitu)", value="forever")
])
async def dodaj_konto(
    interaction: discord.Interaction,
    login: str,
    haslo: str,
    jezyk: app_commands.Choice[str] = None,
    waznosc: app_commands.Choice[str] = None
):
    await interaction.response.defer(ephemeral=True)
    
    lang_val = jezyk.value if jezyk else "auto"
    expires_at = None
    waznosc_desc = "♾️ Na zawsze"

    if waznosc and waznosc.value == "7d":
        exp_dt = datetime.now() + timedelta(days=7)
        expires_at = exp_dt.strftime("%Y-%m-%d %H:%M:%S")
        waznosc_desc = f"📅 7 dni (do `{expires_at[:10]}`)"
    elif waznosc and waznosc.value == "30d":
        exp_dt = datetime.now() + timedelta(days=30)
        expires_at = exp_dt.strftime("%Y-%m-%d %H:%M:%S")
        waznosc_desc = f"📅 30 dni (do `{expires_at[:10]}`)"

    database.add_or_update_account(
        discord_user_id=interaction.user.id,
        login=login,
        password=haslo,
        language=lang_val,
        auto_daily=1,
        expires_at=expires_at
    )

    embed = discord.Embed(
        title="✅ Konto zapisane pomyślnie!",
        description=f"Konto `{login}` zostało dodane do bota. Będzie rozwiązywać się codziennie rano.",
        color=discord.Color.green()
    )
    embed.add_field(name="🎯 Język", value=f"`{lang_val.upper()}`", inline=True)
    embed.add_field(name="⏳ Ważność", value=waznosc_desc, inline=True)
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
        description="Lista zarejestrowanych kont i ich status ważności:",
        color=discord.Color.blue()
    )

    now = datetime.now()
    for i, acc in enumerate(accounts, start=1):
        last_str = acc["last_run"] or "Brak danych"
        expires_at = acc.get("expires_at")

        if expires_at and str(expires_at).lower() not in ("none", "null", ""):
            try:
                exp_dt = datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S")
                if now > exp_dt:
                    exp_str = f"❌ **WYGASŁO** (dnia `{expires_at[:10]}`)"
                else:
                    days_left = (exp_dt - now).days
                    if days_left >= 1:
                        exp_str = f"⏳ Ważne do: `{expires_at[:10]}` (pozostało: **{days_left} dni**)"
                    else:
                        hours_left = max(0, int((exp_dt - now).total_seconds() // 3600))
                        exp_str = f"⏳ Ważne do: `{expires_at[:16]}` (pozostało: **{hours_left} godz.**)"
            except Exception:
                exp_str = f"⏳ Ważne do: `{expires_at[:10]}`"
        else:
            exp_str = "♾️ Ważność: **Na zawsze**"

        embed.add_field(
            name=f"{i}. Login: `{acc['login']}`",
            value=f"🎯 Język: `{acc['language'].upper()}`\n{exp_str}\n🕒 Ostatnia sesja: `{last_str}`",
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

    # Weryfikacja ważności czasowej kont
    active_targets = []
    expired_logins = []
    for a in targets:
        if database.is_account_active(a):
            active_targets.append(a)
        else:
            expired_logins.append(a["login"])

    if not active_targets:
        exp_list = ", ".join([f"`{l}`" for l in expired_logins])
        await interaction.followup.send(
            f"❌ Wskazane konto/konta ({exp_list}) **wygasły**! Użyj `/dodaj_konto`, aby odnowić ich ważność.",
            ephemeral=True
        )
        return

    targets = active_targets
    total = len(targets)
    status_embed = discord.Embed(
        title="⏳ Rozwiązywanie sesji InstaLing...",
        description=f"Rozpoczynam sesje dla **{total}** kont w losowych paczkach...",
        color=discord.Color.gold()
    )
    msg = await interaction.followup.send(embed=status_embed)

    results = []
    remaining = list(targets)
    batch_idx = 1
    done_count = 0

    while remaining:
        # Losowa wielkość paczki od config.BATCH_SIZE_MIN do config.BATCH_SIZE_MAX (np. 2 do 5)
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
            await asyncio.sleep(idx_in_batch * random.uniform(8.0, 15.0))
            r = await solver.solve_session(
                login=acc["login"],
                password=acc["password"],
                preferred_lang=acc.get("language", "auto")
            )
            # Jeśli sesja nie powiodła się z powodu błędu sieci/timeoutu (ale nie błędnego hasła), ponów próbę
            if not r["success"] and not r.get("already_done") and "niepoprawny login" not in r.get("message", "").lower():
                await asyncio.sleep(random.uniform(10.0, 16.0))
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

    if expired_logins:
        summary_embed.add_field(
            name="⚠️ Pominięte konta (wygasłe)",
            value=", ".join([f"`{l}`" for l in expired_logins]),
            inline=False
        )

    summary_embed.set_footer(text="InstaLing AutoBot • dc.zinzelekk")
    await msg.edit(embed=summary_embed)

@bot.tree.command(name="baza", description="Sprawdź liczbę zapamiętanych słówek w bazie Firebase")
async def baza(interaction: discord.Interaction):
    await interaction.response.defer()
    await firebase_client.fetch_database()

    de_count = len(firebase_client.cache.get("de", {}))
    en_count = len(firebase_client.cache.get("en", {}))

    embed = discord.Embed(
        title="☁️ Baza Słówek Firebase (Wspólna ze skryptem)",
        color=discord.Color.purple()
    )
    embed.add_field(name="🇩🇪 Język Niemiecki (DE)", value=f"**{de_count}** słówek", inline=True)
    embed.add_field(name="🇬🇧 Język Angielski (EN)", value=f"**{en_count}** słówek", inline=True)
    embed.add_field(name="📊 Łącznie w chmurze", value=f"**{de_count + en_count}** słówek", inline=False)
    embed.set_footer(text="Baza aktualizuje się automatycznie w czasie rzeczywistym")

    await interaction.followup.send(embed=embed)

# ===== ZARZĄDZANIE KLUCZAMI LICENCYJNYMI DO SKRYPTU =====

@bot.tree.command(name="generuj_klucz", description="Wygeneruj klucz VIP do skryptu InstaLing (tydzień, miesiąc, na zawsze)")
@app_commands.describe(
    czas="Czas trwania licencji skryptu",
    ilosc="Liczba kluczy do wygenerowania (1 - 10)"
)
@app_commands.choices(czas=[
    app_commands.Choice(name="Tydzień (7 dni)", value="WEEK"),
    app_commands.Choice(name="Miesiąc (30 dni)", value="MONTH"),
    app_commands.Choice(name="Na zawsze (LIFETIME)", value="LIFETIME"),
    app_commands.Choice(name="Dzień (24 godziny)", value="DAY")
])
async def generuj_klucz(
    interaction: discord.Interaction,
    czas: app_commands.Choice[str],
    ilosc: app_commands.Range[int, 1, 10] = 1
):
    await interaction.response.defer(ephemeral=True)

    generated_keys = []
    creator_tag = f"{interaction.user.name} ({interaction.user.id})"
    for _ in range(ilosc):
        k = await firebase_client.create_license(license_type=czas.value, creator=creator_tag)
        if k:
            generated_keys.append(k)

    if not generated_keys:
        await interaction.followup.send("❌ Wystąpił błąd podczas generowania kluczy w Firebase.", ephemeral=True)
        return

    czas_labels = {
        "WEEK": "📅 Tydzień (7 dni od aktywacji)",
        "MONTH": "📅 Miesiąc (30 dni od aktywacji)",
        "LIFETIME": "👑 Na zawsze (LIFETIME)",
        "DAY": "⏱️ Dzień (24h od aktywacji)"
    }
    label = czas_labels.get(czas.value, czas.value)

    keys_formatted = "\n".join([f"`{k}`" for k in generated_keys])
    embed = discord.Embed(
        title=f"🔑 Wygenerowano Klucz VIP do Skryptu ({len(generated_keys)})",
        color=discord.Color.gold()
    )
    embed.add_field(name="⏳ Typ ważności", value=label, inline=False)
    embed.add_field(name="🎟️ Klucze (kliknij aby skopiować)", value=keys_formatted, inline=False)
    embed.add_field(
        name="📌 Jak aktywować w skrypcie",
        value=(
            "1. Zainstaluj i otwórz skrypt Tampermonkey na [instaling.pl](https://instaling.pl).\n"
            "2. Wklej wygenerowany klucz w okienku licencji.\n"
            "3. Klucz przypisze się do Twojej przeglądarki (1 klucz = 1 urządzenie).\n"
            "*(W panelu bocznym skryptu można sprawdzić status lub zmienić klucz).*"
        ),
        inline=False
    )
    embed.set_footer(text="InstaLing VIP System • dc.zinzelekk")

    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="klucze_skryptu", description="Wyświetl listę kluczy licencyjnych do skryptu w Firebase")
async def klucze_skryptu(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    licenses = await firebase_client.get_all_licenses()
    if not licenses:
        await interaction.followup.send("ℹ️ W bazie Firebase nie ma obecnie żadnych kluczy licencyjnych skryptu.", ephemeral=True)
        return

    unused = []
    bound = []

    now_ms = int(time.time() * 1000)

    for k, v in licenses.items():
        if not isinstance(v, dict):
            continue
        status = v.get("status", "unused")
        lic_type = v.get("type", "WEEK")
        exp_ms = v.get("expiresAt", -1)

        if status == "unused":
            unused.append((k, lic_type))
        else:
            if exp_ms == -1:
                exp_desc = "♾️ Lifetime"
            elif exp_ms < now_ms:
                exp_desc = "❌ Wygasł"
            else:
                days_left = max(0, int((exp_ms - now_ms) / (1000 * 3600 * 24)))
                exp_desc = f"⏳ {days_left}d"
            bound.append((k, lic_type, exp_desc))

    embed = discord.Embed(
        title=f"🔐 Baza Kluczy Licencyjnych Skryptu ({len(licenses)})",
        color=discord.Color.blue()
    )
    embed.add_field(name="📊 Statystyki", value=f"🟢 Niewykorzystane: **{len(unused)}** | 🔒 Aktywne: **{len(bound)}**", inline=False)

    if unused:
        preview_unused = unused[:15]
        u_lines = [f"`{k}` — **{t}**" for k, t in preview_unused]
        if len(unused) > 15:
            u_lines.append(f"... i jeszcze {len(unused) - 15} wolnych")
        embed.add_field(name=f"🟢 Wolne klucze ({len(unused)})", value="\n".join(u_lines), inline=False)

    if bound:
        preview_bound = bound[:15]
        b_lines = [f"`{k}` — **{t}** ({exp})" for k, t, exp in preview_bound]
        if len(bound) > 15:
            b_lines.append(f"... i jeszcze {len(bound) - 15} aktywnych")
        embed.add_field(name=f"🔒 Aktywne klucze ({len(bound)})", value="\n".join(b_lines), inline=False)

    embed.set_footer(text="InstaLing VIP System • dc.zinzelekk")
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="usun_klucz", description="Usuń klucz licencyjny do skryptu z bazy Firebase")
@app_commands.describe(klucz="Klucz do usunięcia (np. VIP-ABCD-1234)")
async def usun_klucz(interaction: discord.Interaction, klucz: str):
    await interaction.response.defer(ephemeral=True)

    clean_key = klucz.strip().upper()
    success = await firebase_client.delete_license(clean_key)
    if success:
        await interaction.followup.send(f"✅ Klucz `{clean_key}` został pomyślnie usunięty z bazy Firebase.", ephemeral=True)
    else:
        await interaction.followup.send(f"❌ Nie udało się usunąć klucza `{clean_key}` z bazy Firebase.", ephemeral=True)

def run():
    token = config.DISCORD_TOKEN
    if not token or token == "WSTAW_TUTAJ_TOKEN_BOTA_DISCORD":
        print("=" * 60)
        print("❌ BŁĄD: Nie ustawiono tokena bota Discord!")
        print("Wklej swój token w pliku .env lub config.py w zmiennej DISCORD_TOKEN.")
        print("Token zdobędziesz na: https://discord.com/developers/applications")
        print("=" * 60)
        return

    bot.run(token)

if __name__ == "__main__":
    run()
