import discord
from discord.ui import Modal, TextInput, View, Button
from redbot.core import commands, Config
from redbot.core.bot import Red
import aiohttp
import asyncio
import time
from discord.ext import tasks
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)

class Auction(commands.Cog):
    """A cog to handle auctions with bidding and donations."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)
        self.config.register_global(auctions={})
        self.config.register_global(bids={})
        self.auction_task.start()

    @commands.Cog.listener()
    async def on_ready(self):
        logging.info(f'Logged in as {self.bot.user}')

    async def api_check(self, interaction: discord.Interaction, item_count, item_name) -> bool:
        """Check if the donated item meets the value requirements."""
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get("https://api.gwapes.com/items") as response:
                    if response.status != 200:
                        await interaction.response.send_message("Error fetching item value from API. Please try again later.", ephemeral=True)
                        logging.error(f"API response status: {response.status}")
                        return False
                    
                    data = await response.json()
                    items = data.get("body", [])
                    item_data = next((item for item in items if item["name"].strip().lower() == item_name.strip().lower()), None)
                    
                    if not item_data:
                        await interaction.response.send_message("Item not found. Please enter a valid item name.", ephemeral=True)
                        return False
                    
                    item_value = item_data.get("value", 0)
                    total_value = item_value * item_count
                    
                    if total_value < 100000000:  # Changed to 100 million
                        await interaction.response.send_message("The total donation value must be over 100 million.", ephemeral=True)
                        return False

            except Exception as e:
                await interaction.response.send_message(f"An error occurred while fetching item value: {str(e)}", ephemeral=True)
                logging.error(f"Exception in API check: {e}")
                return False
        return True

    def get_next_auction_id(self):
        """Generate the next auction ID."""
        auctions = self.bot.loop.run_until_complete(self.config.auctions())
        return str(max(map(int, auctions.keys()), default=0) + 1)

    class AuctionModal(Modal):
        def __init__(self, cog):
            self.cog = cog
            super().__init__(title="Request An Auction")

        item_name = TextInput(
            label="What are you going to donate?",
            placeholder="e.g., Blob",
            required=True,
            min_length=1,
            max_length=100,
        )
        item_count = TextInput(
            label="How many of those items will you donate?",
            placeholder="e.g., 5",
            required=True,
            max_length=10,
        )
        minimum_bid = TextInput(
            label="What should the minimum bid be?",
            placeholder="e.g., 1,000,000",
            required=False,
        )
        message = TextInput(
            label="What is your message?",
            placeholder="e.g., I love DR!",
            required=False,
            max_length=200,
        )

        async def on_submit(self, interaction: discord.Interaction):
            """Handle the form submission."""
            try:
                item_name = self.item_name.value
                item_count = self.item_count.value

                if not item_count.isdigit():
                    await interaction.response.send_message("Item count must be a number.", ephemeral=True)
                    return

                item_count = int(item_count)
                valid = await self.cog.api_check(interaction, item_count, item_name)
                
                if not valid:
                    return

                guild = interaction.guild
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(read_messages=False),
                    interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
                    self.cog.bot.user: discord.PermissionOverwrite(read_messages=True),
                }
                
                ticket_channel = await guild.create_text_channel(f"ticket-{interaction.user.name}", overwrites=overwrites)
                await ticket_channel.send(f"{interaction.user.mention}, please donate {item_count} of {item_name} as you have mentioned in the modal or you will get blacklisted.")
                await interaction.response.send_message("Auction details submitted! Please donate the items within 30 minutes.", ephemeral=True)

                auction_id = self.cog.get_next_auction_id()

                auction_data = {
                    "auction_id": auction_id,
                    "user_id": interaction.user.id,
                    "item": item_name,
                    "amount": item_count,
                    "min_bid": self.minimum_bid.value or "1,000,000",
                    "message": self.message.value,
                    "status": "pending",
                    "ticket_channel_id": ticket_channel.id
                }

                async with self.cog.config.auctions() as auctions:
                    auctions[auction_id] = auction_data

                # Send auction details with lock button
                item_value = item_count * 16375000  # Example calculation for item value
                fee = item_value * 0.02  # 2% fee
                embed = discord.Embed(
                    title="Your Auction Detail",
                    description=f"**{item_count}x {item_name}**\n"
                                f"**Minimum bid:** {self.minimum_bid.value or '1,000,000'}\n"
                                f"**Channeltype:** NORMAL\n"
                                f"Total worth: {item_value:,}\n"  
                                f"Your fee (2%): {fee:,}\n"
                                "Type `/auction makechanges` to make changes",
                    color=discord.Color.blue()
                )
                await ticket_channel.send(embed=embed, view=self.cog.TicketView(ticket_channel))

            except Exception as e:
                logging.error(f"An error occurred in modal submission: {e}")
                await interaction.response.send_message(f"An error occurred while processing your submission: {str(e)}", ephemeral=True)

    class TicketView(View):
        def __init__(self, channel):
            super().__init__(timeout=None)
            self.channel = channel

        @discord.ui.button(label="", style=discord.ButtonStyle.secondary, emoji="🔒")
        async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
            await self.channel.delete()
            await interaction.response.send_message("Ticket closed.", ephemeral=True)

    class AuctionView(View):
        def __init__(self, cog):
            super().__init__(timeout=None)
            self.cog = cog

        @discord.ui.button(label="Request Auction", style=discord.ButtonStyle.green)
        async def request_auction_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            """Open the auction request modal."""
            try:
                modal = self.cog.AuctionModal(self.cog)
                await interaction.response.send_modal(modal)
            except Exception as e:
                logging.error(f"An error occurred while sending the modal: {e}")
                await interaction.response.send_message(f"An error occurred while sending the modal: {str(e)}", ephemeral=True)

    @commands.command()
    async def requestauction(self, ctx: commands.Context):
        """Request a new auction."""
        view = self.AuctionView(self)
        embed = discord.Embed(
            title="🎉 Request an Auction 🎉",
            description="Click the button below to request an auction and submit your donation details.",
            color=discord.Color.blue()
        )
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.add_field(name="How it works", value="1. Click the button below.\n2. Fill out the modal with donation details.\n3. Await further instructions in your private channel.", inline=False)
        embed.set_footer(text="Thank you for contributing to our community!")
        await ctx.send(embed=embed, view=view)
        logging.info("Auction request initiated.")

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        """Handle message edits related to auction donations."""
        try:
            if after.author.id == 270904126974590976 and before.embeds != after.embeds:
                auctions = await self.config.auctions()
                title = after.embeds[0].title
                desc = after.embeds[0].description
                
                if title != "Action Confirmed":
                    return
                
                parts = desc.split("**")
                item_dank = str(parts[1].split(">")[1])
                amount_dank = int(parts[1].split("<")[0])
                
                for auction_id, auction in auctions.items():
                    if auction["status"] == "pending" and auction["item"] == item_dank and auction["amount"] == amount_dank:
                        auction["status"] = "active"
                        auction["end_time"] = int(time.time()) + 1800
                        await self.config.auctions.set_raw(auction_id, value=auction)
                        
                        user = self.bot.get_user(auction["user_id"])
                        if user:
                            await user.send("Donation confirmed. Your auction is now active and will last for 30 minutes.")
                        
                        ticket_channel = self.bot.get_channel(auction["ticket_channel_id"])
                        if ticket_channel:
                            await ticket_channel.send("Donation confirmed. Your auction is now active.")
        except Exception as e:
            logging.error(f"An error occurred in on_message_edit listener: {e}")

    @tasks.loop(seconds=60)
    async def auction_task(self):
        """Periodic task to check for auction expirations."""
        auctions = await self.config.auctions()
        current_time = int(time.time())
        for auction_id, auction in auctions.items():
            if auction["status"] == "active" and current_time >= auction["end_time"]:
                auction["status"] = "ended"
                await self.config.auctions.set_raw(auction_id, value=auction)

                user = self.bot.get_user(auction["user_id"])
                if user:
                    await user.send("Your auction has ended.")
                
                ticket_channel = self.bot.get_channel(auction["ticket_channel_id"])
                if ticket_channel:
                    await ticket_channel.send("The auction has ended.")
                    await ticket_channel.delete()
        logging.info("Auction task executed.")

    @auction_task.before_loop
    async def before_auction_task(self):
        await self.bot.wait_until_ready()

async def setup(bot: Red):
    await bot.add_cog(Auction(bot))
