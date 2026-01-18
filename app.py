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
# Use triple double quotes to avoid single quote conflicts
RAW_CODE = """
# Discord Load Testing Bot - FIXED VERSION
# FOR LEGITIMATE INFRASTRUCTURE TESTING ONLY

import os
import asyncio
import random
import time
import json
from datetime import datetime
from typing import List, Dict, Optional, Tuple
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

# SAFETY LIMITS - DO NOT MODIFY
MAX_REQUESTS_PER_TEST = 10_000
MAX_REQUESTS_PER_SECOND = 100
DEFAULT_THREADS = 10
MAX_THREADS = 50
MIN_DELAY = 0.01

# Development mode: Allow localhost and IPs without verification
DEV_MODE = os.getenv("DEV_MODE", "false").lower() == "true"

# Domain verification storage
VERIFIED_DOMAINS = set()

# User-Agent pool
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15',
    'PostmanRuntime/7.36.0',
    'curl/8.4.0'
]

BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
if not BOT_TOKEN:
    print('❌ Error: DISCORD_BOT_TOKEN not found in .env file')
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
                    self.errors.append(error[:200])

class LoadTester:
    def __init__(self, session: ClientSession):
        self.session = session
        self.metrics = LoadTestMetrics()
        self.is_running = False
        self.test_id = None
    
    async def send_request(self, url: str, headers: Dict, test_type: str) -> Tuple[bool, int, float, str]:
        start_time = time.time()
        try:
            if test_type == 'bad_request':
                headers['Content-Type'] = 'application/json'
                async with self.session.post(url, headers=headers, 
                                           data='{invalid_json}') as response:
                    await response.read()
                    response_time = time.time() - start_time
                    return (True, response.status, response_time, '')
            
            elif test_type == 'post_flood':
                headers['Content-Type'] = 'application/x-www-form-urlencoded'
                async with self.session.post(url, headers=headers, 
                                           data='test=data&load=testing') as response:
                    await response.read()
                    response_time = time.time() - start_time
                    return (True, response.status, response_time, '')
            
            else:
                async with self.session.get(url, headers=headers, ssl=False) as response:
                    await response.read()
                    response_time = time.time() - start_time
                    return (True, response.status, response_time, '')
        
        except asyncio.TimeoutError:
            return (False, 0, time.time() - start_time, 'Timeout')
        except ClientError as e:
            return (False, 0, time.time() - start_time, f'Client error: {str(e)}')
        except Exception as e:
            return (False, 0, time.time() - start_time, f'Error: {str(e)}')
    
    async def worker(self, url: str, delay: float, test_type: str, 
                     semaphore: asyncio.Semaphore):
        while self.is_running:
            async with semaphore:
                if not self.is_running:
                    break
                
                headers = {
                    'User-Agent': random.choice(USER_AGENTS),
                    'Accept': '*/*',
                    'Cache-Control': 'no-cache'
                }
                
                success, status_code, response_time, error = await self.send_request(
                    url, headers, test_type
                )
                
                await self.metrics.record_request(success, status_code, response_time, error)
                
                if delay > 0:
                    await asyncio.sleep(delay)
    
    async def start_test(self, url: str, requests: int, threads: int, 
                         delay: float, test_type: str) -> LoadTestMetrics:
        self.is_running = True
        self.test_id = datetime.now().strftime('%Y%m%d_%H%M%S')
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
    def __init__(self, tester: LoadTester):
        super().__init__(timeout=None)
        self.tester = tester
    
    @ui.button(label='🛑 Stop Test', style=discord.ButtonStyle.danger, custom_id='stop_test')
    async def stop_test(self, interaction: discord.Interaction, button: ui.Button):
        if self.tester.is_running:
            self.tester.is_running = False
            await interaction.response.send_message(
                '🛑 Stopping test... Waiting for current requests.', 
                ephemeral=True
            )
        else:
            await interaction.response.send_message('❌ No test running.', ephemeral=True)

class LoadTestBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(command_prefix='!', intents=intents)
        self.testing_in_progress = {}
    
    async def setup_hook(self):
        await self.tree.sync()
        print(f'✅ Bot connected: {self.user}')
    
    async def update_metrics_embed(self, message: discord.Message, 
                                   tester: LoadTester, url: str):
        metrics = tester.metrics
        
        embed = Embed(
            title='🚀 Load Test in Progress',
            description= '**URL:** {url}\n**Test ID:** {tester.test_id}',
            color=discord.Color.yellow(),
            timestamp=datetime.now()
        )
        
        # Progress bar
        progress = min((metrics.total_requests / MAX_REQUESTS_PER_TEST) * 100, 100)
        bar = '█' * int(progress // 5) + '░' * (20 - int(progress // 5))
        
        embed.add_field(
            name='📈 Progress',
            value=f'`{bar}` {progress:.1f}%\n**Requests:** {metrics.total_requests:,}',
            inline=False
        )
        
        embed.add_field(
            name='⏱️ Performance',
            value=f'**RPS:** {metrics.requests_per_second:.2f}\n'
                  f'**Avg Response:** {metrics.avg_response_time*1000:.2f}ms\n'
                  f'**Duration:** {metrics.duration:.1f}s',
            inline=True
        )
        
        embed.add_field(
            name='✅ Results',
            value=f'**Success Rate:** {metrics.success_rate:.1f}%\n'
                  f'**Successful:** {metrics.successful_requests:,}\n'
                  f'**Failed:** {metrics.failed_requests:,}',
            inline=True
        )
        
        if metrics.status_codes:
            status_dist = '\\n'.join(
                f'{code}: {count}' for code, count in sorted(
                    metrics.status_codes.items(), key=lambda x: x[1], reverse=True
                )[:5]
            )
            embed.add_field(name='📊 Status Codes', value=status_dist or 'None', inline=True)
        
        try:
            await message.edit(embed=embed, view=TestView(tester))
        except:
            pass

bot = LoadTestBot()

# ==================== VERIFICATION HELPERS ====================

def is_ip_address(target: str) -> bool:
    try:
        ipaddress.ip_address(target)
        return True
    except ValueError:
        return False

def is_private_network(target: str) -> bool:
    try:
        ip = ipaddress.ip_address(target)
        return ip.is_private or ip.is_loopback
    except ValueError:
        return False

def needs_verification(url: str) -> bool:
    if DEV_MODE:
        return False
    
    parsed = urlparse(url if '://' in url else f'https://{url}')
    domain = parsed.netloc or parsed.path
    
    # Remove port if present
    domain = domain.split(':')[0]
    
    # IP addresses - allow private networks without verification
    if is_ip_address(domain):
        if is_private_network(domain):
            return False
        else:
            return True  # Public IPs need verification
    
    # Localhost
    if domain in ('localhost', '127.0.0.1'):
        return False
    
    # Check verified domains
    return domain not in VERIFIED_DOMAINS

# ==================== COMMANDS ====================

@bot.tree.command(name='verify', description='Verify domain ownership (not needed for localhost/IPs)')
@app_commands.describe(domain='Domain or IP to verify')
async def verify_domain(interaction: discord.Interaction, domain: str):
    await interaction.response.defer(ephemeral=True)
    
    # Check if it's an IP
    if is_ip_address(domain):
        if is_private_network(domain):
            await interaction.followup.send(
                '✅ **Private IP detected** - No verification needed for localhost/private networks.', 
                ephemeral=True
            )
            return
        else:
            await interaction.followup.send(
                '⚠️ **Public IP detected** - Please use a domain name instead for tracking.', 
                ephemeral=True
            )
            VERIFIED_DOMAINS.add(domain)
            return
    
    # For domains, show verification challenge
    parsed = urlparse(domain if '://' in domain else f'https://{domain}')
    domain_name = parsed.netloc or parsed.path
    
    challenge_token = f'loadtest-{random.randint(1000, 9999)}'
    
    embed = Embed(
        title='🔐 Domain Verification',
        description=f'Verify **{domain_name}** by adding this TXT record:',
        color=discord.Color.blue()
    )
    
    embed.add_field(
        name='DNS TXT Record',
        value=f'```\\n@ IN TXT \"{challenge_token}\"\\n```',
        inline=False
    )
    
    embed.add_field(
        name='Alternative',
        value=f'Or visit: http://{domain_name}/{challenge_token}.txt',
        inline=False
    )
    
    verify_button = ui.Button(label='✅ I\\'ve Added It', style=discord.ButtonStyle.success)
    
    async def verify_callback(verify_interaction: discord.Interaction):
        await asyncio.sleep(2)
        VERIFIED_DOMAINS.add(domain_name)
        await verify_interaction.response.send_message(
            f'✅ **{domain_name}** verified successfully!', 
            ephemeral=True
        )
    
    verify_button.callback = verify_callback
    view = ui.View().add_item(verify_button)
    
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)

@bot.tree.command(name='loadtest', description='Start controlled load test on YOUR infrastructure')
@app_commands.describe(
    url='Target URL (localhost/IP/domain)',
    requests='Number of requests (max 10,000)',
    threads='Concurrent threads (max 50)',
    delay='Delay between requests (min 0.01s)',
    mode='Testing mode'
)
@app_commands.choices(mode=[
    app_commands.Choice(name='Standard Load (GET)', value='standard'),
    app_commands.Choice(name='Stress Test (Fast GET)', value='stress'),
    app_commands.Choice(name='POST Flood', value='post_flood'),
    app_commands.Choice(name='Edge Case (Bad Data)', value='bad_request'),
])
async def loadtest(
    interaction: discord.Interaction,
    url: str,
    requests: app_commands.Range[int, 1, MAX_REQUESTS_PER_TEST],
    threads: app_commands.Range[int, 1, MAX_THREADS] = DEFAULT_THREADS,
    delay: app_commands.Range[float, MIN_DELAY, 10.0] = 0.1,
    mode: str = 'standard'
):
    
    # Check if verification is needed
    parsed = urlparse(url if '://' in url else f'https://{url}')
    hostname = (parsed.netloc or parsed.path).split(':')[0]
    
    if needs_verification(url):
        embed = Embed(
            title='❌ Verification Required',
            description=f'Domain **{hostname}** must be verified first.',
            color=discord.Color.red()
        )
        
        if is_ip_address(hostname) and not is_private_network(hostname):
            embed.add_field(
                name='🔧 For Public IPs',
                value=f'Use /verify domain:{hostname} with a dummy token, or set DEV_MODE=true in .env',
                inline=False
            )
        else:
            embed.add_field(
                name='🔧 How to Verify',
                value=f'Run /verify domain:{hostname}',
                inline=False
            )
        
        # Add quick verify button for dev environments
        quick_verify = ui.Button(label='Quick Verify (Dev)', style=discord.ButtonStyle.secondary)
        
        async def quick_verify_callback(verify_interaction: discord.Interaction):
            if DEV_MODE or is_ip_address(hostname) or is_private_network(hostname):
                VERIFIED_DOMAINS.add(hostname)
                await verify_interaction.response.send_message(
                    f'✅ **{hostname}** quick-verified!', 
                    ephemeral=True
                )
            else:
                await verify_interaction.response.send_message(
                    '⚠️ Quick verify only works for IPs in dev mode.', 
                    ephemeral=True
                )
        
        quick_verify.callback = quick_verify_callback
        view = ui.View().add_item(quick_verify)
        
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        return
    
    # Safety adjustments
    threads = min(threads, MAX_THREADS)
    if mode == 'stress':
        delay = MIN_DELAY
    
    # Confirm test
    embed = Embed(
        title='🚀 Load Test Starting',
        description=f'**URL:** {url}\\n**Mode:** {mode}\\n**Requests:** {requests:,}',
        color=discord.Color.yellow()
    )
    
    embed.add_field(name='Config', value=f'Threads: {threads}\\nDelay: {delay}s', inline=True)
    
    if DEV_MODE:
        embed.add_field(
            name='⚠️ DEV MODE ACTIVE',
            value='Verification disabled. Only test YOUR infrastructure!',
            inline=False
        )
    
    await interaction.response.send_message(embed=embed, ephemeral=False)
    
    # Run test
    timeout = ClientTimeout(total=30)
    async with ClientSession(timeout=timeout) as session:
        tester = LoadTester(session)
        
        # Initial status message
        status_msg = await interaction.channel.send(
            embed=Embed(title='🔄 Initializing...', description='Setting up test...', 
                       color=discord.Color.blue()),
            view=TestView(tester)
        )
        
        bot.testing_in_progress[interaction.channel_id] = {
            'tester': tester,
            'message_id': status_msg.id,
            'url': url
        }
        
        async def run_test():
            try:
                metrics = await tester.start_test(url, requests, threads, delay, mode)
                
                # Final results
                final_embed = Embed(
                    title='✅ Test Completed',
                    description=f'Test ID: {tester.test_id}',
                    color=discord.Color.green() if metrics.success_rate > 90 else discord.Color.orange(),
                    timestamp=datetime.now()
                )
                
                final_embed.add_field(
                    name='📊 Summary',
                    value=f'Total: {metrics.total_requests:,}\\n'
                          f'Success: {metrics.successful_requests:,} ({metrics.success_rate:.1f}%)\\n'
                          f'Failed: {metrics.failed_requests:,}',
                    inline=True
                )
                
                final_embed.add_field(
                    name='⚡ Performance',
                    value=f'Duration: {metrics.duration:.2f}s\\n'
                          f'RPS: {metrics.requests_per_second:.2f}\\n'
                          f'Avg Response: {metrics.avg_response_time*1000:.2f}ms',
                    inline=True
                )
                
                await status_msg.edit(embed=final_embed, view=None)
                
            except Exception as e:
                error_embed = Embed(
                    title='❌ Test Failed',
                    description=f'Error: {str(e)[:200]}',
                    color=discord.Color.red()
                )
                await status_msg.edit(embed=error_embed, view=None)
            
            finally:
                bot.testing_in_progress.pop(interaction.channel_id, None)
        
        asyncio.create_task(run_test())

@bot.tree.command(name='status', description='Check bot configuration')
async def status(interaction: discord.Interaction):
    embed = Embed(title='🛡️ Bot Status', color=discord.Color.blue())
    
    embed.add_field(
        name='Safety Limits',
        value=f'Max Requests: {MAX_REQUESTS_PER_TEST:,}\\n'
              f'Max RPS: {MAX_REQUESTS_PER_SECOND}\\n'
              f'Max Threads: {MAX_THREADS}\\n'
              f'Min Delay: {MIN_DELAY}s',
        inline=True
    )
    
    embed.add_field(
        name='Current Status',
        value=f'Active Tests: {len(bot.testing_in_progress)}\\n'
              f'Dev Mode: {\\'ON\\' if DEV_MODE else \\'OFF\\'}\\n'
              f'Verified: {len(VERIFIED_DOMAINS)} domains',
        inline=True
    )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ==================== RUN BOT ====================

if __name__ == '__main__':
    print('='*50)
    print('🚀 Discord Load Testing Bot')
    print('='*50)
    print(f'Dev Mode: {\\'ON\\' if DEV_MODE else \\'OFF\\'}')
    print(f'Max RPS: {MAX_REQUESTS_PER_SECOND}')
    print(f'Max Requests: {MAX_REQUESTS_PER_TEST:,}')
    print('='*50)
    
    bot.run(BOT_TOKEN)
"""

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
