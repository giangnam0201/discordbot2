import streamlit as st
import threading
import os
import asyncio
import sys
import time

# --- 1. THE "ULTIMATE" GLOBAL LOCK ---
if "bot_lock" not in sys.modules:
    sys.modules["bot_lock"] = True
    FIRST_RUN = True
else:
    FIRST_RUN = False

# --- 2. STREAMLIT UI ---
st.set_page_config(page_title="Bot Server", page_icon="🚀")
st.title("Service Status: Online ✅")
st.write("The bot is running in the background.")

# BRIDGE: Injects Streamlit Secrets into the environment
for key, value in st.secrets.items():
    os.environ[key] = str(value)

# --- 3. YOUR CODE ---
# Use triple single quotes to avoid conflicts
RAW_CODE = '''
# Discord Load Testing Bot - FIXED FOR STREAMLIT
import os
import asyncio
import random
import time
import json
from datetime import datetime
from urllib.parse import urlparse
import ipaddress

import discord
from discord import app_commands, ui, Embed, File
from discord.ext import commands
import aiohttp
from aiohttp import ClientSession, ClientTimeout, ClientError
from dotenv import load_dotenv

# ==================== CONFIGURATION ====================

load_dotenv()

# SAFETY LIMITS
MAX_REQUESTS_PER_TEST = 10000
MAX_REQUESTS_PER_SECOND = 100
DEFAULT_THREADS = 10
MAX_THREADS = 50
MIN_DELAY = 0.01

# Development mode
DEV_MODE = os.getenv("DEV_MODE", "false").lower() == "true"

# Domain verification storage
VERIFIED_DOMAINS = set()

# User-Agent pool
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15",
    "PostmanRuntime/7.36.0",
    "curl/8.4.0"
]

BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if not BOT_TOKEN:
    print("Error: DISCORD_BOT_TOKEN not found")
    exit(1)

# ==================== LOAD TESTER ENGINE ====================

class LoadTestMetrics:
    def __init__(self):
        self.start_time = time.time()
        self.end_time = None
        self.total_requests = 0
        self.successful_requests = 0
        self.failed_requests = 0
        self.status_codes = {}
        self.response_times = []
        self.errors = []
        self.lock = asyncio.Lock()
    
    @property
    def duration(self):
        end = self.end_time or time.time()
        return end - self.start_time
    
    @property
    def requests_per_second(self):
        return self.total_requests / max(self.duration, 0.001)
    
    @property
    def success_rate(self):
        if self.total_requests == 0:
            return 0
        return (self.successful_requests / self.total_requests) * 100
    
    @property
    def avg_response_time(self):
        if not self.response_times:
            return 0
        return sum(self.response_times) / len(self.response_times)
    
    async def record_request(self, success, status_code, response_time, error=None):
        async with self.lock:
            self.total_requests += 1
            if success:
                self.successful_requests += 1
                self.status_codes[status_code] = self.status_codes.get(status_code, 0) + 1
                self.response_times.append(response_time)
            else:
                self.failed_requests += 1
                if error:
                    self.errors.append(error[:200])

class LoadTester:
    def __init__(self, session):
        self.session = session
        self.metrics = LoadTestMetrics()
        self.is_running = False
        self.test_id = None
    
    async def send_request(self, url, headers, test_type):
        start_time = time.time()
        try:
            if test_type == "bad_request":
                headers["Content-Type"] = "application/json"
                async with self.session.post(url, headers=headers, data="{invalid_json}") as response:
                    await response.read()
                    response_time = time.time() - start_time
                    return (True, response.status, response_time, "")
            
            elif test_type == "post_flood":
                headers["Content-Type"] = "application/x-www-form-urlencoded"
                async with self.session.post(url, headers=headers, data="test=data&load=testing") as response:
                    await response.read()
                    response_time = time.time() - start_time
                    return (True, response.status, response_time, "")
            
            else:
                async with self.session.get(url, headers=headers, ssl=False) as response:
                    await response.read()
                    response_time = time.time() - start_time
                    return (True, response.status, response_time, "")
        
        except asyncio.TimeoutError:
            return (False, 0, time.time() - start_time, "Timeout")
        except ClientError as e:
            return (False, 0, time.time() - start_time, f"Client error: {str(e)}")
        except Exception as e:
            return (False, 0, time.time() - start_time, f"Error: {str(e)}")
    
    async def worker(self, url, delay, test_type, semaphore):
        while self.is_running:
            async with semaphore:
                if not self.is_running:
                    break
                
                headers = {
                    "User-Agent": random.choice(USER_AGENTS),
                    "Accept": "*/*",
                    "Cache-Control": "no-cache"
                }
                
                success, status_code, response_time, error = await self.send_request(
                    url, headers, test_type
                )
                
                await self.metrics.record_request(success, status_code, response_time, error)
                
                if delay > 0:
                    await asyncio.sleep(delay)
    
    async def start_test(self, url, requests, threads, delay, test_type):
        self.is_running = True
        self.test_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.metrics = LoadTestMetrics()
        
        threads = min(threads, MAX_THREADS)
        semaphore = asyncio.Semaphore(threads)
        workers = []
        
        reqs_per_worker = requests // threads
        extra_reqs = requests % threads
        
        for i in range(threads):
            worker_reqs = reqs_per_worker + (1 if i < extra_reqs else 0)
            if worker_reqs > 0:
                worker_task = asyncio.create_task(
                    self.worker(url, delay, test_type, semaphore)
                )
                workers.append(worker_task)
        
        try:
            await asyncio.gather(*workers)
        except asyncio.CancelledError:
            self.is_running = False
            await asyncio.gather(*workers, return_exceptions=True)
        
        self.metrics.end_time = time.time()
        self.is_running = False
        return self.metrics

# ==================== DISCORD BOT ====================

class TestView(ui.View):
    def __init__(self, tester):
        super().__init__(timeout=None)
        self.tester = tester
    
    @ui.button(label="Stop Test", style=discord.ButtonStyle.danger)
    async def stop_test(self, interaction, button):
        if self.tester.is_running:
            self.tester.is_running = False
            await interaction.response.send_message("Stopping test...", ephemeral=True)
        else:
            await interaction.response.send_message("No test running.", ephemeral=True)

class LoadTestBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)
        self.testing_in_progress = {}
    
    async def setup_hook(self):
        await self.tree.sync()
        print(f"Bot connected: {self.user}")
    
    async def update_metrics_embed(self, message, tester, url):
        metrics = tester.metrics
        
        embed = Embed(
            title="Load Test in Progress",
            description=f"URL: {url}\\nTest ID: {tester.test_id}",
            color=discord.Color.yellow(),
            timestamp=datetime.now()
        )
        
        # Progress bar
        progress = min((metrics.total_requests / MAX_REQUESTS_PER_TEST) * 100, 100)
        bar = "█" * int(progress // 5) + "░" * (20 - int(progress // 5))
        
        embed.add_field(
            name="Progress",
            value=f"Bar: {bar} {progress:.1f}%\\nRequests: {metrics.total_requests}",
            inline=False
        )
        
        embed.add_field(
            name="Performance",
            value=f"RPS: {metrics.requests_per_second:.2f}\\n"
                  f"Avg Response: {metrics.avg_response_time*1000:.2f}ms\\n"
                  f"Duration: {metrics.duration:.1f}s",
            inline=True
        )
        
        embed.add_field(
            name="Results",
            value=f"Success Rate: {metrics.success_rate:.1f}%\\n"
                  f"Successful: {metrics.successful_requests}\\n"
                  f"Failed: {metrics.failed_requests}",
            inline=True
        )
        
        try:
            await message.edit(embed=embed, view=TestView(tester))
        except:
            pass

bot = LoadTestBot()

# ==================== VERIFICATION HELPERS ====================

def is_ip_address(target):
    try:
        ipaddress.ip_address(target)
        return True
    except:
        return False

def is_private_network(target):
    try:
        ip = ipaddress.ip_address(target)
        return ip.is_private or ip.is_loopback
    except:
        return False

def needs_verification(url):
    if DEV_MODE:
        return False
    
    parsed = urlparse(url if "://" in url else f"https://{url}")
    domain = parsed.netloc or parsed.path
    domain = domain.split(":")[0]
    
    if is_ip_address(domain):
        if is_private_network(domain):
            return False
        else:
            return True
    
    if domain in ("localhost", "127.0.0.1"):
        return False
    
    return domain not in VERIFIED_DOMAINS

# ==================== COMMANDS ====================

@bot.tree.command(name="verify", description="Verify domain ownership")
async def verify_domain(interaction, domain):
    await interaction.response.defer(ephemeral=True)
    
    if is_ip_address(domain):
        if is_private_network(domain):
            await interaction.followup.send("Private IP - no verification needed", ephemeral=True)
            return
        else:
            VERIFIED_DOMAINS.add(domain)
            await interaction.followup.send("Public IP added", ephemeral=True)
            return
    
    parsed = urlparse(domain if "://" in domain else f"https://{domain}")
    domain_name = parsed.netloc or parsed.path
    
    embed = Embed(
        title="Domain Verification",
        description=f"Verify {domain_name}",
        color=discord.Color.blue()
    )
    
    embed.add_field(name="Method", value="Add DNS TXT record or HTTP file", inline=False)
    
    verify_button = ui.Button(label="Verify", style=discord.ButtonStyle.success)
    
    async def verify_callback(verify_interaction):
        VERIFIED_DOMAINS.add(domain_name)
        await verify_interaction.response.send_message(f"Verified {domain_name}", ephemeral=True)
    
    verify_button.callback = verify_callback
    view = ui.View().add_item(verify_button)
    
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)

@bot.tree.command(name="loadtest", description="Start load test")
@app_commands.describe(
    url="Target URL",
    requests="Number of requests",
    threads="Concurrent threads",
    delay="Delay between requests",
    mode="Testing mode"
)
async def loadtest(
    interaction,
    url,
    requests,
    threads=DEFAULT_THREADS,
    delay=0.1,
    mode="standard"
):
    hostname = urlparse(url if "://" in url else f"https://{url}").netloc or urlparse(url).path
    hostname = hostname.split(":")[0]
    
    if needs_verification(url):
        embed = Embed(title="Verification Required", description=f"Verify {hostname} first", color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    threads = min(threads, MAX_THREADS)
    if mode == "stress":
        delay = MIN_DELAY
    
    embed = Embed(title="Load Test Starting", description=f"URL: {url}", color=discord.Color.yellow())
    await interaction.response.send_message(embed=embed, ephemeral=False)
    
    timeout = ClientTimeout(total=30)
    async with ClientSession(timeout=timeout) as session:
        tester = LoadTester(session)
        
        status_msg = await interaction.channel.send(
            embed=Embed(title="Initializing...", color=discord.Color.blue()),
            view=TestView(tester)
        )
        
        bot.testing_in_progress[interaction.channel_id] = {
            "tester": tester,
            "message_id": status_msg.id,
            "url": url
        }
        
        async def run_test():
            try:
                metrics = await tester.start_test(url, requests, threads, delay, mode)
                
                final_embed = Embed(title="Test Completed", color=discord.Color.green())
                final_embed.add_field(name="Total", value=metrics.total_requests)
                final_embed.add_field(name="Success Rate", value=f"{metrics.success_rate:.1f}%")
                
                await status_msg.edit(embed=final_embed, view=None)
                
            except Exception as e:
                error_embed = Embed(title="Test Failed", description=str(e)[:200], color=discord.Color.red())
                await status_msg.edit(embed=error_embed, view=None)
            
            finally:
                bot.testing_in_progress.pop(interaction.channel_id, None)
        
        asyncio.create_task(run_test())

@bot.tree.command(name="status", description="Check bot status")
async def status(interaction):
    embed = Embed(title="Bot Status", color=discord.Color.blue())
    embed.add_field(name="Active Tests", value=len(bot.testing_in_progress))
    embed.add_field(name="Dev Mode", value="ON" if DEV_MODE else "OFF")
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ==================== RUN BOT ====================

if __name__ == "__main__":
    print("Starting bot...")
    bot.run(BOT_TOKEN)
'''

# --- 4. STARTUP ENGINE ---
def run_bot():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    exec(RAW_CODE, globals())

if FIRST_RUN:
    thread = threading.Thread(target=run_bot, daemon=True)
    thread.start()
    st.success("🚀 Bot launched for the first time!")
else:
    st.info("ℹ️ Bot is already running in the background.")

st.divider()
st.caption(f"Last page refresh: {time.strftime('%H:%M:%S')}")
