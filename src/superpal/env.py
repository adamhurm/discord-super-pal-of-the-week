import logging, os                # standard
from dotenv import load_dotenv    # 3rd-party
from . import static as superpal_static  # super pal

###########
# Logging #
########### 
log = logging.getLogger('super-pal')
log.setLevel(logging.INFO)
log_handler = logging.FileHandler(filename='discord-super-pal.log', encoding='utf-8', mode='w')
dt_fmt = '%Y-%m-%d %H:%M:%S'
formatter = logging.Formatter('[{asctime}] [{levelname:<8}] {name}: {message}', dt_fmt, style='{')
log_handler.setFormatter(formatter)
log.addHandler(log_handler)

##################
# Env. variables #
##################
load_dotenv()
TOKEN = os.environ['SUPERPAL_TOKEN']
GUILD_ID = int(os.environ['GUILD_ID'])
EMOJI_GUILD_ID = GUILD_ID if os.environ['EMOJI_GUILD_ID'] is None else int(os.environ['EMOJI_GUILD_ID'])
CHANNEL_ID = int(os.environ['CHANNEL_ID'])
ART_CHANNEL_ID = CHANNEL_ID if os.environ['ART_CHANNEL_ID'] is None else int(os.environ['ART_CHANNEL_ID'])
GPT_ASSISTANT_ID = os.environ['GPT_ASSISTANT_ID']
GPT_ASSISTANT_THREAD_ID = os.environ['GPT_ASSISTANT_THREAD_ID']
OPENAI_API_KEY = os.environ['OPENAI_API_KEY']

base_reqnotmet = TOKEN is None or GUILD_ID is None or CHANNEL_ID is None
ai_reqnotmet = os.environ['OPENAI_API_KEY'] is None
RUNTIME_WARN_MSG = 'WARN: Super Pal will still run but you are very likely to encounter run-time errors.'
if base_reqnotmet:
    log.warn(f'Base requirements not fulfilled. Please provide TOKEN, GUILD_ID, CHANNEL_ID.\n'
             f'{superpal_static.RUNTIME_WARN_MSG}\n')
if ai_reqnotmet:
    log.warn(f'OpenAI requirements not fulfilled. Please provide api key.\n'
             f'{superpal_static.RUNTIME_WARN_MSG}\n')
