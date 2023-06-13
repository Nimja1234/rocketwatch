import logging
import re
from datetime import datetime, timedelta, timezone
from io import BytesIO

import openai
from discord import Object, File, DeletedReferencedMessage
from discord.app_commands import guilds
from discord.ext import commands
from discord.ext.commands import Context, is_owner
from discord.ext.commands import hybrid_command
from transformers import GPT2TokenizerFast

from utils.cfg import cfg
from utils.embeds import Embed

log = logging.getLogger("openai")
log.setLevel(cfg["log_level"])


class OpenAi(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        openai.api_key = cfg["openai.secret"]
        # log all possible engines
        models = openai.Model.list()
        log.debug([d.id for d in models.data])
        self.engine = "gpt-3.5-turbo-16k"
        self.tokenizer = GPT2TokenizerFast.from_pretrained("gpt2")
        self.last_summary_dict = {}
        self.last_financial_advice_dict = {}

    @classmethod
    def message_to_text(cls, message):
        text = f"user {message.author.name} at {message.created_at.strftime('%H:%M')}:\n {message.content}"
        # if there is an image attached, add it to the text as a note
        metadata = []
        if message.attachments:
            metadata.append(f"{len(message.attachments)} attachments")
        if message.embeds:
            metadata.append(f"{len(message.embeds)} embeds")
        # replies and make sure the reference is not deleted
        if message.reference and not isinstance(message.reference.resolved, DeletedReferencedMessage) and message.reference.resolved:
            # show name of referenced message author
            # and the first 10 characters of the referenced message
            metadata.append(f"reply to \"{message.reference.resolved.content[:32]}…\" from {message.reference.resolved.author.name}")
        if metadata:
            text += f" <{', '.join(metadata)}>\n"
        # replace all <@[0-9]+> with the name of the user
        for mention in message.mentions:
            text = text.replace(f"<@{mention.id}>", f"@{mention.name}")
        # remove all emote ids, i.e change <:emote_name:emote_id> to <:emote_name> using regex
        text = re.sub(r":[0-9]+>", ":>", text)
        return text

    @hybrid_command()
    async def summarize_chat(self, ctx: Context):
        await ctx.defer(ephemeral=True)
        # ratelimit
        if self.last_summary_dict.get(ctx.channel.id) is not None and (datetime.now(timezone.utc) - self.last_summary_dict.get(ctx.channel.id)) < timedelta(minutes=15):
            await ctx.send("You can only summarize once every 15 minutes.", ephemeral=True)
            return
        if ctx.channel.id not in [405163713063288832]:
            await ctx.send("You can't summarize here.", ephemeral=True)
            return
        last_ts = self.last_summary_dict.get(ctx.channel.id) or datetime(2021, 1, 1, tzinfo=timezone.utc)
        response, prompt = await self.prompt_model(ctx.channel, "The following is a short summary of the above chat log:", last_ts)
        e = Embed()
        e.title = "Chat Summarization"
        e.description = response["choices"][0]["message"]["content"]
        token_usage = response['usage']['total_tokens']
        e.set_footer(text=f"Request cost: ${token_usage / 1000 * 0.003:.2f} | Tokens: {token_usage} | /donate if you like this command")
        # attach the prompt as a file
        f = BytesIO(prompt.encode("utf-8"))
        f.name = "prompt.txt"
        f = File(f, filename=f"prompt_log_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.txt")
        # send message in the channel
        await ctx.send("done")
        await ctx.channel.send(embed=e, file=f)
        self.last_summary_dict[ctx.channel.id] = datetime.now(timezone.utc)

    # a function that generates the prompt for the model by taking an array of messages, a prefix and a suffix
    def generate_prompt(self, messages, prefix, suffix):
        messages.sort(key=lambda x: x.created_at)
        prompt = "\n".join([self.message_to_text(message) for message in messages]).replace("\n\n", "\n")
        return f"{prefix}\n\n{prompt}\n\n{suffix}"

    async def prompt_model(self, channel, prompt, cut_off_ts):
        messages = [message async for message in channel.history(limit=1024) if message.content != ""]
        messages = [message for message in messages if message.author.id != self.bot.user.id]
        messages = [message for message in messages if message.created_at > cut_off_ts]
        if len(messages) < 32:
            return None, None
        prefix = "The following is a chat log. Everything prefixed with `>` is a quote."
        print(len(self.tokenizer(self.generate_prompt(messages, prefix, prompt))['input_ids']))
        while len(self.tokenizer(self.generate_prompt(messages, prefix, prompt))['input_ids']) > (16384 - 512):
            # remove the oldest message
            messages.pop(0)

        prompt = self.generate_prompt(messages, prefix, prompt)
        response = openai.ChatCompletion.create(
            model=self.engine,
            max_tokens=512,
            temperature=0.7,
            top_p=1.0,
            frequency_penalty=0.0,
            presence_penalty=1,
            messages=[{"role": "user", "content": prompt}]
        )
        return response, prompt


async def setup(self):
    await self.add_cog(OpenAi(self))