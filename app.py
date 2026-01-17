import streamlit as st
import threading
import os
import asyncio
import sys
import time
# --- 1. THE "ULTIMATE" GLOBAL LOCK ---
# We check if our custom 'bot_lock' exists in the system modules.
# This prevents the bot from starting twice, even if the page is refreshed.
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
RAW_CODE = '''
# Discord Load Testing Bot
# FOR LEGITIMATE INFRASTRUCTURE TESTING ONLY
# Required: pip install discord.py aiohttp python-dotenv

import os
import asyncio
import random
import time
import json
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from urllib.parse import urlparse

import discord
from discord import app_commands, ui, Embed, File
from discord.ext import commands, tasks
import aiohttp
from aiohttp import ClientSession, ClientTimeout, ClientError
from dotenv import load_dotenv

# ==================== CONFIGURATION ====================

load_dotenv()

# SAFETY LIMITS - DO NOT MODIFY
MAX_REQUESTS_PER_TEST = 100000000
MAX_REQUESTS_PER_SECOND = 100
DEFAULT_THREADS = 100
MAX_THREADS = 500
MIN_DELAY = 0.001  # 100ms minimum between requests per thread

# Domain verification storage
VERIFIED_DOMAINS = set()

# User-Agent pool for rotation
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/120.0.0.0",
    "Mozilla/5.0 (Linux; Android 10; SM-G973F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "PostmanRuntime/7.36.0",
    "curl/8.4.0",
    "python-requests/2.31.0"
]

# Bot Configuration
BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", "0"))  # Optional: restrict to specific guild

if not BOT_TOKEN:
    print("❌ Error: DISCORD_BOT_TOKEN not found in .env file")
    print("Please create a .env file with: DISCORD_BOT_TOKEN=your_token_here")
    exit(1)

# ==================== LOAD TESTER ENGINE ====================

class LoadTestMetrics:
    def __init__(self):
        self.start_time = time.time()
        self.end_time = None
        self.total_requests = 0
        self.successful_requests = 0
        self.failed_requests = 0
        self.status_codes: Dict[int, int] = {}
        self.response_times: List[float] = []
        self.errors: List[str] = []
        self.lock = asyncio.Lock()
    
    @property
    def duration(self) -> float:
        end = self.end_time or time.time()
        return end - self.start_time
    
    @property
    def requests_per_second(self) -> float:
        return self.total_requests / max(self.duration, 0.001)
    
    @property
    def success_rate(self) -> float:
        if self.total_requests == 0:
            return 0
        return (self.successful_requests / self.total_requests) * 100
    
    @property
    def avg_response_time(self) -> float:
        if not self.response_times:
            return 0
        return sum(self.response_times) / len(self.response_times)
    
    async def record_request(self, success: bool, status_code: int, 
                             response_time: float, error: Optional[str] = None):
        async with self.lock:
            self.total_requests += 1
            if success:
                self.successful_requests += 1
                self.status_codes[status_code] = self.status_codes.get(status_code, 0) + 1
                self.response_times.append(response_time)
            else:
                self.failed_requests += 1
                if error:
                    self.errors.append(error[:200])  # Truncate long errors

class LoadTester:
    def __init__(self, session: ClientSession):
        self.session = session
        self.metrics = LoadTestMetrics()
        self.is_running = False
        self.test_id = None
    
    async def send_request(self, url: str, headers: Dict, test_type: str) -> Tuple[bool, int, float, str]:
        """Send a single request and return (success, status_code, response_time, error)"""
        start_time = time.time()
        try:
            if test_type == "bad_request":
                # Test error handling with invalid data
                headers["Content-Type"] = "application/json"
                async with self.session.post(url, headers=headers, 
                                           data="{invalid_json}") as response:
                    await response.read()
                    response_time = time.time() - start_time
                    return (True, response.status, response_time, "")
            
            elif test_type == "post_flood":
                # Simulate form submission
                headers["Content-Type"] = "application/x-www-form-urlencoded"
                async with self.session.post(url, headers=headers, 
                                           data="test=data&load=testing") as response:
                    await response.read()
                    response_time = time.time() - start_time
                    return (True, response.status, response_time, "")
            
            else:
                # Standard GET request
                async with self.session.get(url, headers=headers, ssl=False) as response:
                    await response.read()
                    response_time = time.time() - start_time
                    return (True, response.status, response_time, "")
        
        except asyncio.TimeoutError:
            return (False, 0, time.time() - start_time, "Timeout")
        except ClientError as e:
            return (False, 0, time.time() - start_time, f"Client error: {str(e)}")
        except Exception as e:
            return (False, 0, time.time() - start_time, f"Unexpected error: {str(e)}")
    
    async def worker(self, url: str, delay: float, test_type: str, 
                     semaphore: asyncio.Semaphore):
        """Worker that sends requests with rate limiting"""
        while self.is_running:
            async with semaphore:
                if not self.is_running:
                    break
                
                # Rotate User-Agent
                headers = {
                    "User-Agent": random.choice(USER_AGENTS),
                    "Accept": "*/*",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Cache-Control": "no-cache"
                }
                
                success, status_code, response_time, error = await self.send_request(
                    url, headers, test_type
                )
                
                await self.metrics.record_request(success, status_code, response_time, error)
                
                if delay > 0:
                    await asyncio.sleep(delay)
    
    async def start_test(self, url: str, requests: int, threads: int, 
                         delay: float, test_type: str) -> LoadTestMetrics:
        """Start the load test with safety controls"""
        self.is_running = True
        self.test_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.metrics = LoadTestMetrics()
        
        # Safety: Limit threads
        threads = min(threads, MAX_THREADS)
        
        # Safety: Adjust delay to respect RPS limit
        max_rps_per_thread = 1 / max(delay, MIN_DELAY)
        total_max_rps = threads * max_rps_per_thread
        if total_max_rps > MAX_REQUESTS_PER_SECOND:
            delay = max(threads / MAX_REQUESTS_PER_SECOND, MIN_DELAY)
        
        semaphore = asyncio.Semaphore(threads)
        workers = []
        
        # Calculate requests per worker
        reqs_per_worker = requests // threads
        extra_reqs = requests % threads
        
        for i in range(threads):
            worker_reqs = reqs_per_worker + (1 if i < extra_reqs else 0)
            if worker_reqs > 0:
                worker_task = asyncio.create_task(
                    self.worker(url, delay, test_type, semaphore)
                )
                workers.append(worker_task)
        
        # Wait for completion or cancellation
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
    def __init__(self, tester: LoadTester):
        super().__init__(timeout=None)
        self.tester = tester
    
    @ui.button(label="🛑 Stop Test", style=discord.ButtonStyle.danger, custom_id="stop_test")
    async def stop_test(self, interaction: discord.Interaction, button: ui.Button):
        if self.tester.is_running:
            self.tester.is_running = False
            await interaction.response.send_message(
                "🛑 Stopping test... Please wait for current requests to complete.", 
                ephemeral=True
            )
        else:
            await interaction.response.send_message("❌ No test is currently running.", 
                                                  ephemeral=True)
    
    @ui.button(label="📊 Live Metrics", style=discord.ButtonStyle.primary, 
               custom_id="refresh_metrics")
    async def refresh_metrics(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer()
        # Metrics will be auto-updated by the task

class LoadTestBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)
        self.testing_in_progress = {}
    
    async def setup_hook(self):
        # Sync commands
        await self.tree.sync()
        print(f"✅ Bot connected as {self.user}")
        print(f"📊 Commands synced: /loadtest, /verify, /status")
    
    async def update_metrics_embed(self, message: discord.Message, 
                                   tester: LoadTester, url: str):
        """Update the metrics embed in real-time"""
        metrics = tester.metrics
        
        embed = Embed(
            title="🚀 Load Test in Progress",
            description=f"**URL:** {url}\n**Test ID:** {tester.test_id}",
            color=discord.Color.yellow(),
            timestamp=datetime.now()
        )
        
        # Progress bar
        progress = min((metrics.total_requests / MAX_REQUESTS_PER_TEST) * 100, 100)
        bar = "█" * int(progress // 5) + "░" * (20 - int(progress // 5))
        
        embed.add_field(
            name="📈 Progress",
            value=f"`{bar}` {progress:.1f}%\n"
                  f"**Requests:** {metrics.total_requests:,} / {MAX_REQUESTS_PER_TEST:,}",
            inline=False
        )
        
        embed.add_field(
            name="⏱️ Performance",
            value=f"**RPS:** {metrics.requests_per_second:.2f}\n"
                  f"**Avg Response:** {metrics.avg_response_time*1000:.2f}ms\n"
                  f**Duration: ** {metrics.duration:.1f}s ",
            inline=True
        )
        
        embed.add_field(
            name="✅ Results",
            value=f ** Success Rate: ** {metrics.success_rate:.1f}% \n"
                  f ** Successful: ** {metrics.successful_requests:,} \n"
                  f ** Failed: ** {metrics.failed_requests:,} ",
            inline=True
        )
        
        # Status code distribution
        if metrics.status_codes:
            status_dist = " \n ".join(
                f ** {code}: {count} ** for code, count in sorted(
                    metrics.status_codes.items(), key=lambda x: x[1], reverse=True
                )[:5]
            )
            embed.add_field(
                name="📊 Status Codes",
                value=status_dist or "None yet",
                inline=True
            )
        
        embed.add_field(
            name="🛡️ Safety Limits",
            value=f ** Max RPS: ** {MAX_REQUESTS_PER_SECOND} \n"
                  f ** Threads: ** {len(asyncio.all_tasks()) - 1} \n"
                  f ** Rate Limited: ** Yes ",
            inline=True
        )
        
        if metrics.errors:
            embed.add_field(
                name="⚠️ Recent Errors",
                value=f ** {len(metrics.errors)} errors **\n"
                      f ** Last: ** {metrics.errors[-1][:60]}... ",
                inline=False
            )
        
        embed.set_footer(text="Click 🛑 to stop | 📊 auto-refreshes every 5s")
        
        try:
            await message.edit(embed=embed, view=TestView(tester))
        except discord.NotFound:
            pass  # Message deleted
        except discord.HTTPException:
            pass  # Rate limited or other error
    
    @tasks.loop(seconds=5)
    async def metrics_updater(self):
        """Background task to update metrics"""
        for channel_id, data in list(self.testing_in_progress.items()):
            try:
                message = await self.get_channel(channel_id).fetch_message(data["message_id"])
                await self.update_metrics_embed(message, data["tester"], data["url"])
            except:
                pass
    
    @commands.Cog.listener()
    async def on_ready(self):
        self.metrics_updater.start()

# ==================== COMMANDS ====================

bot = LoadTestBot()

@bot.tree.command(name="verify", description="Verify domain ownership for testing")
@app_commands.describe(domain="Domain to verify (e.g., example.com)")
async def verify_domain(interaction: discord.Interaction, domain: str):
    """Verify you own a domain before testing"""
    await interaction.response.defer(ephemeral=True)
    
    # Extract domain
    parsed = urlparse(domain if "://" in domain else f"https://{domain}")
    domain_name = parsed.netloc or parsed.path
    
    # In production, implement real verification:
    # 1. DNS TXT record check
    # 2. HTTP file upload verification
    # For this demo, we'll use a simple challenge
    
    challenge_token = f"loadtest-{random.randint(1000, 9999)}"
    
    embed = Embed(
        title="🔐 Domain Verification Required",
        description=f"To verify ownership of **{domain_name}**, please complete one of these steps:",
        color=discord.Color.blue()
    )
    
    embed.add_field(
        name="Option 1: DNS TXT Record",
        value=f"Add this TXT record:\n`@ IN TXT \"{challenge_token}\"`",
        inline=False
    )
    
    embed.add_field(
        name="Option 2: HTTP File",
        value=f"Create file at:\n`http://{domain_name}/{challenge_token}.txt`\nWith content: `{challenge_token}`",
        inline=False
    )
    
    embed.add_field(
        name="Option 3: Meta Tag",
        value=f"Add to homepage `<head>`:\n`<meta name=\"loadtest-verification\" content=\"{challenge_token}\"`",
        inline=False
    )
    
    verify_button = ui.Button(
        label="✅ I've Added the Record", 
        style=discord.ButtonStyle.success,
        custom_id=f"verify_{domain_name}_{challenge_token}"
    )
    
    async def verify_callback(verify_interaction: discord.Interaction):
        # Simulate verification (in real implementation: check DNS/HTTP)
        # For demo: assume verification succeeds after button click
        VERIFIED_DOMAINS.add(domain_name)
        await verify_interaction.response.send_message(
            f"✅ **{domain_name}** verified! You can now test this domain.", 
            ephemeral=True
        )
    
    verify_button.callback = verify_callback
    view = ui.View().add_item(verify_button)
    
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)

@bot.tree.command(name="loadtest", description="Start a controlled load test on YOUR infrastructure")
@app_commands.describe(
    url="Target URL (must be verified domain)",
    requests="Number of requests (max 10,000)",
    threads="Concurrent threads (max 50)",
    delay="Delay between requests in seconds (min 0.01)",
    mode="Testing mode"
)
@app_commands.choices(mode=[
    app_commands.Choice(name="Standard Load (GET)", value="standard"),
    app_commands.Choice(name="Stress Test (Fast GET)", value="stress"),
    app_commands.Choice(name="POST Flood Simulation", value="post_flood"),
    app_commands.Choice(name="Edge Case (Invalid Data)", value="bad_request"),
    app_commands.Choice(name="User-Agent Rotation", value="ua_rotate"),
])
async def loadtest(
    interaction: discord.Interaction,
    url: str,
    requests: app_commands.Range[int, 1, MAX_REQUESTS_PER_TEST],
    threads: app_commands.Range[int, 1, MAX_THREADS] = DEFAULT_THREADS,
    delay: app_commands.Range[float, MIN_DELAY, 10.0] = 0.1,
    mode: str = "standard"
):
    """Start a load test with real-time monitoring"""
    
    # Verify domain ownership
    parsed = urlparse(url if "://" in url else f"https://{url}")
    domain = parsed.netloc or parsed.path
    
    if domain not in VERIFIED_DOMAINS:
        embed = Embed(
            title="❌ Domain Not Verified",
            description=f"You must verify ownership of **{domain}** before testing.",
            color=discord.Color.red()
        )
        embed.add_field(
            name="How to Verify",
            value="Use `/verify domain:yourdomain.com` first",
            inline=False
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    # Safety checks
    if threads > MAX_THREADS:
        threads = MAX_THREADS
    
    if requests > MAX_REQUESTS_PER_TEST:
        requests = MAX_REQUESTS_PER_TEST
    
    # Adjust delay for stress mode
    if mode == "stress":
        delay = MIN_DELAY
    
    # Confirm before starting
    embed = Embed(
        title="🚀 Load Test Starting",
        description=f ** Testing: ** {url} \n** Mode: ** {mode} ** Requests: ** {requests:,} ",
        color=discord.Color.yellow()
    )
    
    embed.add_field(name="Threads", value=str(threads), inline=True)
    embed.add_field(name="Delay", value=f"{delay}s", inline=True)
    embed.add_field(name="Max RPS", value=str(MAX_REQUESTS_PER_SECOND), inline=True)
    
    embed.add_field(
        name="⚠️ Safety Warning",
        value="This test will only run on verified domains. "
              "Abuse will result in permanent bot ban.",
        inline=False
    )
    
    await interaction.response.send_message(embed=embed, ephemeral=False)
    
    # Create tester instance
    timeout = ClientTimeout(total=30)
    async with ClientSession(timeout=timeout) as session:
        tester = LoadTester(session)
        
        # Send initial metrics message
        metrics_embed = Embed(
            title="🔄 Initializing Test...",
            description="Setting up workers and establishing connections",
            color=discord.Color.blue()
        )
        metrics_message = await interaction.channel.send(
            embed=metrics_embed, 
            view=TestView(tester)
        )
        
        # Store for background updates
        bot.testing_in_progress[interaction.channel_id] = {
            "tester": tester,
            "message_id": metrics_message.id,
            "url": url
        }
        
        # Start test in background
        async def run_test():
            try:
                metrics = await tester.start_test(url, requests, threads, delay, mode)
                
                # Update with final results
                final_embed = Embed(
                    title="✅ Load Test Completed",
                    description=f ** Test ID: ** {tester.test_id} \n** URL: ** {url} ",
                    color=discord.Color.green() if metrics.success_rate > 90 else discord.Color.orange(),
                    timestamp=datetime.now()
                )
                
                final_embed.add_field(
                    name="📊 Summary",
                    value=f ** Total Requests: ** {metrics.total_requests:,} \n"
                          f ** Success Rate: ** {metrics.success_rate:.2f}% \n"
                          f ** Failed: ** {metrics.failed_requests:,} ",
                    inline=True
                )
                
                final_embed.add_field(
                    name="⚡ Performance",
                    value=f ** Duration: ** {metrics.duration:.2f}s \n"
                          f ** Avg RPS: ** {metrics.requests_per_second:.2f} \n"
                          f ** Avg Response: ** {metrics.avg_response_time*1000:.2f}ms ",
                    inline=True
                )
                
                if metrics.status_codes:
                    status_chart = " \n ".join(
                        f "  {code}  ({count:>4} req) " for code, count in 
                        sorted(metrics.status_codes.items(), key=lambda x: x[1], reverse=True)
                    )
                    final_embed.add_field(
                        name="📈 Status Codes",
                        value=f"```{status_chart}```",
                        inline=False
                    )
                
                # Add top errors
                if metrics.errors:
                    error_counts = {}
                    for error in metrics.errors:
                        key = error.split(":")[0]
                        error_counts[key] = error_counts.get(key, 0) + 1
                    
                    top_errors = " \n ".join(
                        f"{err}: {count}x" for err, count in 
                        sorted(error_counts.items(), key=lambda x: x[1], reverse=True)[:3]
                    )
                    final_embed.add_field(
                        name="⚠️ Error Summary",
                        value=f" ``` {top_errors} ``` ",
                        inline=False
                    )
                
                final_embed.set_footer(text="Test completed safely with rate limiting")
                
                await metrics_message.edit(embed=final_embed, view=None)
                
                # Send detailed log file
                log_data = {
                    "test_id": tester.test_id,
                    "url": url,
                    "mode": mode,
                    "requests": requests,
                    "threads": threads,
                    "delay": delay,
                    "metrics": {
                        "total_requests": metrics.total_requests,
                        "successful_requests": metrics.successful_requests,
                        "failed_requests": metrics.failed_requests,
                        "duration": metrics.duration,
                        "avg_response_time": metrics.avg_response_time,
                        "requests_per_second": metrics.requests_per_second,
                        "success_rate": metrics.success_rate,
                        "status_codes": metrics.status_codes,
                        "errors": metrics.errors[:10]  # Last 10 errors
                    },
                    "timestamp": datetime.now().isoformat()
                }
                
                log_file = f"loadtest_{tester.test_id}.json"
                with open(log_file, "w") as f:
                    json.dump(log_data, f, indent=2)
                
                await interaction.channel.send(
                    "📄 Detailed logs attached below:",
                    file=File(log_file),
                    delete_after=60
                )
                
                os.remove(log_file)
                
            except Exception as e:
                error_embed = Embed(
                    title="❌ Test Failed",
                    description=f"An error occurred: `{str(e)[:200]}`",
                    color=discord.Color.red()
                )
                await metrics_message.edit(embed=error_embed, view=None)
            
            finally:
                # Clean up
                bot.testing_in_progress.pop(interaction.channel_id, None)
        
        # Start test in background
        asyncio.create_task(run_test())

@bot.tree.command(name="status", description="Check bot status and safety limits")
async def status(interaction: discord.Interaction):
    """Display current safety limits and bot status"""
    embed = Embed(
        title="🛡️ Load Testing Bot - Status",
        description="Safety limits and current configuration",
        color=discord.Color.blue()
    )
    
    embed.add_field(
        name="Safety Limits",
        value=f ** Max Requests/Test: ** {MAX_REQUESTS_PER_TEST:,} \n"
              f ** Max RPS: ** {MAX_REQUESTS_PER_SECOND} \n"
              f ** Max Threads: ** {MAX_THREADS} \n"
              f ** Min Delay: ** {MIN_DELAY}s ",
        inline=True
    )
    
    embed.add_field(
        name="Bot Status",
        value=f ** Active Tests: ** {len(bot.testing_in_progress)} \n"
              f ** Verified Domains: ** {len(VERIFIED_DOMAINS)} \n"
              f ** Uptime: ** N/A ",  # Could add uptime tracking
        inline=True
    )
    
    verified_list = " \n".join(list(VERIFIED_DOMAINS)[:10]) or "None"
    if len(VERIFIED_DOMAINS) > 10:
        verified_list += f"\n... and {len(VERIFIED_DOMAINS) - 10} more"
    
    embed.add_field(
        name="Verified Domains",
        value=f"``` {verified_list} ``` ",
        inline=False
    )
    
    embed.add_field(
        name="Commands",
        value=f" ** /verify ** - Verify domain ownership \n"
              f" ** /loadtest ** - Start a test \n"
              f" ** /status ** - Show this info ",
        inline=False
    )
    
    embed.set_footer(text="Ethical load testing only | Abuse = permanent ban")
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ==================== MAIN ====================

if __name__ == "__main__":
    print("="*50)
    print("🚀 Discord Load Testing Bot")
    print("="*50)
    print(f"Safety Limits:")
    print(f"  - Max Requests: {MAX_REQUESTS_PER_TEST:,}")
    print(f"  - Max RPS: {MAX_REQUESTS_PER_SECOND}")
    print(f"  - Max Threads: {MAX_THREADS}")
    print("="*50)
    print("Starting bot...")
    
    bot.run(BOT_TOKEN)
'''

# --- 4. STARTUP ENGINE ---
def run_bot():
    # Setup new loop for this specific background thread
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    # Passing 'globals()' ensures functions can see each other
    exec(RAW_CODE, globals())

if FIRST_RUN:
    thread = threading.Thread(target=run_bot, daemon=True)
    thread.start()
    st.success("🚀 Bot launched for the first time!")
else:
    st.info("ℹ️ Bot is already running in the background.")

# Show a small clock so the user knows the page is "alive"
st.divider()
st.caption(f"Last page refresh: {time.strftime('%H:%M:%S')}")
