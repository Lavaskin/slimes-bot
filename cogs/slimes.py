from ast import alias
import asyncio
import json
import math
import random
import time
import discord
import os
from os.path import exists
from discord.ext import commands
from PIL import Image, ImageFont, ImageDraw
from firebase_admin import credentials, firestore, initialize_app, storage


# ID Constants
ID_BG_VARIENT   = 0
ID_BG_PRIMARY   = 1
ID_BG_SECONDARY = 2
ID_BODY_VARIENT = 3
ID_BODY         = 4
ID_EYES         = 5
ID_MOUTH        = 6
ID_HAT          = 7
# Shop Constants
SLIME_PRICE   = 10
SELLING_RATIO = 1 # Amount to remove from price when selling

# Load Descriptions File
descFile = open('./other/commands.json')
desc = json.loads(descFile.read())

# Get Dev Mode
_env = os.getenv('SLIME_DEV', 'True')
_dev = True if _env == 'True' else False
_cd  = 0 if _dev else 1 # Turn off cooldowns in dev


class Slimes(commands.Cog):
	def __init__(self, bot):
		# Set random class properties
		self.bot = bot
		self.outputDir = './output/dev/' if _dev else './output/prod/'
		self.width, self.height = 200, 200
		self.fontPath = os.getenv('FONT_PATH', 'consola.ttf')
		self.siteLink = os.getenv('SITE_LINK') if not _dev else 'http://localhost:4200/'
		self.desc = desc # Allow access in functions

		# Init Database
		dbCred = credentials.Certificate('./other/firebase.json')
		self.collection = 'users-dev' if _dev else 'users'
		initialize_app(dbCred, {'storageBucket': os.getenv('STORAGE_BUCKET')})
		self.db = firestore.client()
		self.bucket = storage.bucket()

		# Load colors
		self.colors = []
		with open('./res/colors.txt', 'r') as f:
			for line in f.readlines():
				self.colors.append(line.replace('\n', ''))
				f.close()

		# Load roll parameters
		paramsFile = open('./other/params.json')
		self.params = json.loads(paramsFile.read())
		paramsFile.close()

		# Count Parts
		def countFiles(dir):
			# Counts the amount of files in a directory
			return len([f for f in os.listdir(dir) if os.path.isfile(dir + f)])
		self.partsDir      = './res/parts/'
		self.specialBgs    = countFiles(self.partsDir + 'backgrounds/special/')
		self.regBodies     = countFiles(self.partsDir + 'bodies/regular/')
		self.specialBodies = countFiles(self.partsDir + 'bodies/special/')
		self.eyes          = countFiles(self.partsDir + 'face/eyes/')
		self.mouths        = countFiles(self.partsDir + 'face/mouths/')
		self.hats          = countFiles(self.partsDir + 'hats/')
		random.seed()
		print(' > Finished initial setup.')


	#####################
	# Utility Functions #
	#####################

	# Makes a new document for a user if they aren't registered
	def checkUser(self, id, author=None):
		# Check if already registered
		ref = self.db.collection(self.collection).document(id)

		if not ref.get().exists:
			if not author: return False
			# Make document
			data = {'tag': str(author), 'slimes': [], 'favs': [], 'coins': 100, 'pfp': '', 'selling': [], 'lastclaim': 0}
			ref.set(data)
			print(' | Registered: ' + str(author))
			return False
		else:
			# They are already registered
			return True

	# Given a slime ID, determines how rare it is. Returns its rank and rarity number
	def getRarity(self, id):
		text = 'This slimes rarity is unknown...'
		score = 0

		# Check background (solid is 0, stripes is 1 and special is 4)
		if id[ID_BG_VARIENT] == '1': score += 1
		elif id[ID_BG_VARIENT] == '2': score += 6

		# Check if body is special
		if id[ID_BODY_VARIENT] == '1': score += 8

		# Check if the slime doesn't have eyes
		if id[ID_EYES] == 'z': score += 9

		# Check if it has a mouth
		if id[ID_MOUTH] != 'z': score += 1

		# Check if it has a hat
		if id[ID_HAT] != 'z': score += 1

		if score == 0:
			text = 'This is an **extremely ordinary** slime!'
		elif score < 3:
			text = 'This is a **common** slime.'
		elif score < 6:
			text = 'This is an **uncommon** slime.'
		elif score < 9:
			text = 'This is a **rare** slime!'
		elif score < 12:
			text = 'This is a **pretty rare** slime!'
		elif score < 20:
			text = 'This is a **very rare** slime!!'
		elif score >= 20:
			text = 'This is an :sparkles:**overwhelmingly rare** slime!!!'

		return text, score

	# Test if a given parameter randomly passes
	def passesParam(self, param):
		return random.randint(1, 100) < (self.params[param] * 100)

	# Favorites a given slime or removes it if already favorited
	def favSlime(self, id, ref):
		# Check if already in favorites and if favorites are maxed out
		favs = ref.get().to_dict()['favs']
		if id in favs:
			ref.update({'favs': firestore.ArrayRemove([id])})
			return f'**{id}** has been removed from your favorites!'
		elif len(favs) == 9:
			return 'You can only have a max of 9 favorites!'
		else:
			ref.update({'favs': firestore.ArrayUnion([id])})
			return f'**{id}** has been added to your favorites!'

	# Checks if a given slime passes the given filter
	def passesFilter(self, filter, slime):
		# Check if every character passes the filter
		for i, c in enumerate(slime):
			if filter[i] != '?' and filter[i] != c:
				return False
		return True

	# Turns a list into a string with a given character in between
	def formatList(self, list, c):
		res = ''
		for i in list:
			res += (i + c)
		return res[:-1]

	# Encodes a single number
	def encodeNum(self, n):
		if n < 10:
			return str(n)
		if n < 36:
			return chr(n + 55)
		return chr(n + 61)

	# Turn a character from an encoded string into a number
	def decodeChar(self, n):
		if n == 'z':
			return 'z'
		else:
			if ord(n) > 96:
				return ord(n) - 61
			elif ord(n) > 64:
				return ord(n) - 55
			else:
				return int(n)

	# Generates two different paint colors from the global list (RETURNS THEIR INDEX!)
	def getPaintColors(self):
		colorCount = len(self.colors)
		c1 = random.randrange(0, colorCount)
		c2 = random.randrange(0, colorCount)

		# Flip paint color if same as bg
		if c1 == c2:
			c1 = colorCount - c1 - 1
		return c1, c2

	# Given a list of files, creates a layered image of them in order
	# Used to smooth the process of making new image collections
	def rollLayers(self, fName, layers, bgColor):
		# Generate the image
		final = Image.new(mode='RGB', size=(self.width, self.height), color=self.colors[bgColor])

		# Roll Layers
		for file in layers:
			try:
				layer = Image.open(file[0])
			except FileNotFoundError:
				return None

			# Check if the layer needs a transparency mask
			if file[1]:
				final.paste(layer, (0, 0), layer)
			else:
				final.paste(layer)
			layer.close()

		# Save the image/close
		final.save(fName)
		final.close()
		return fName

	# Makes a 3x3 grid of slimes and returns the path to the output image
	def makeCollage(self, userID, slimes):
		numFavs = len(slimes)
		font = ImageFont.truetype(self.fontPath, 20)
		fontLen,  _ = font.getsize('#' + slimes[0])
		width = (3 * self.width) if numFavs > 2 else numFavs * self.width
		height = math.ceil(numFavs / 3) * self.height
		n = 0
		combined = Image.new(mode='RGBA', size=(width, height), color=(0, 0, 0, 0))
		draw = ImageDraw.Draw(combined)
		fName = f'{self.outputDir}{random.randint(100000, 999999)}_{userID}.png'

		for y in range(0, height, self.height):
			for x in range(0, width, self.width):
				if n < numFavs:
					img = Image.open(f'{self.outputDir}{slimes[n]}.png')
					combined.paste(img, (x, y))
					draw.text(((x + self.width) - fontLen, y), f"#{slimes[n]}", (0, 0, 0), font=font)
					n += 1
				else:
					break
		
		# Finish up
		combined.save(fName)
		combined.close()
		return fName

	def timeSince(self, date):
		return math.ceil(time.time() - date)

	# Retuns the minutes, seconds of a time in seconds
	def convertTime(self, secs):
		minutes = int(secs / 60)
		seconds = int(secs % 60)
		return minutes, seconds

	# Returns an object of (claimed coins, error message)
	def claimCoins(self, ref):
		user = ref.get().to_dict()
		coins = user['coins']

		# Check coin count
		if coins >= 9999:
			return 0, 'You have reached the maximum amount of claimable coins!'

		# Check cooldown
		since = self.timeSince(user['lastclaim'])
		left = (desc['claim']['cd'] - since) * _cd
		if left > 0:
			minutes, seconds = self.convertTime(left)
			return 0, f'There\'s *{minutes}m, {seconds}s* left before you can claim coins again!'

		# Calc payout
		# Every 1000 coins collected, lower the payout amount by 10% (Minimum of 10% payout))
		# Only triggers over 500 coins
		payout = 40 + random.randint(-SLIME_PRICE, SLIME_PRICE)
		multiplier = max(round(1 - round((coins / 1000) * 0.1, 3), 3), 0.1) if coins > 500 else 1
		payout = math.ceil(payout * multiplier)

		ref.update({'coins': firestore.Increment(payout)})
		ref.update({'lastclaim': time.time()})
		return payout, None


	########################
	# Generation Functions #
	########################

	# Given a slime ID, creates a slime
	def genSlimeLayers(self, id):
		splitID = [self.decodeChar(c) for c in id]
		layers = []

		# id[0] = background variant (0 = solid, 1 = stripes, 2 = specials)
		# id[1] = primary background (solid color/special bg)
		# id[2] = secondary background color (stripes)
		if splitID[ID_BG_VARIENT] == 1:
			layers.append((f'{self.partsDir}backgrounds/stripes/{splitID[ID_BG_SECONDARY]}.png', True))
		elif splitID[ID_BG_VARIENT] == 2:
			layers.append((f'{self.partsDir}backgrounds/special/{splitID[ID_BG_PRIMARY]}.png', False))

		# id[3] = body varient (0 = normal, 1 = special)
		# id[4] = body
		if splitID[ID_BODY_VARIENT] == 0:
			layers.append((f'{self.partsDir}bodies/regular/{splitID[ID_BODY]}.png', True))
		elif splitID[ID_BODY_VARIENT] == 1:
			layers.append((f'{self.partsDir}bodies/special/{splitID[ID_BODY]}.png', True))
		
		# id[5] = eyes
		if splitID[ID_EYES] != 'z':
			layers.append((f'{self.partsDir}face/eyes/{splitID[ID_EYES]}.png', True))
			
			# id[6] = mouth (Only possible if the slime has eyes)
			if splitID[ID_MOUTH] != 'z':
				layers.append((f'{self.partsDir}face/mouths/{splitID[ID_MOUTH]}.png', True))

		# id[7] = hat
		if splitID[ID_HAT] != 'z':
			layers.append((f'{self.partsDir}hats/{splitID[ID_HAT]}.png', True))

		return layers

	# Based on random parameters, generates a slime ID
	# Returns the ID and background color for rollLayers to use
	def genSlimeID(self):
		# Loops until a unique ID is created
		while True:
			bgColor, altColor = self.getPaintColors()
			id = ''

			# Choose background
			if self.passesParam('bg_special'):
				# Apply special background
				roll = random.randrange(0, self.specialBgs)
				id += ('2' + self.encodeNum(roll) + 'z')
			elif self.passesParam('bg_stripes'):
				# Apply stripe layer
				id += ('1' + self.encodeNum(bgColor) + self.encodeNum(altColor))
			else:
				# Solid Color
				id += ('0' + self.encodeNum(bgColor) + 'z')

			# Add slime body
			if self.passesParam('bg_special'):
				roll = random.randrange(0, self.specialBodies)
				id += ('1' + self.encodeNum(roll))
			else:
				roll = random.randrange(0, self.regBodies)
				id += ('0' + self.encodeNum(roll))

			# Eyes
			if self.passesParam('eyes'):
				roll = random.randrange(0, self.eyes)
				id += self.encodeNum(roll)

				# Mouth (Can only be applied if the slime has eyes)
				if self.passesParam('mouth'):
					roll = random.randrange(0, self.mouths)
					id += self.encodeNum(roll)
				else: id += 'z'
			else: id += 'zz' # For both eyes and mouth

			# Hat
			if self.passesParam('hat'):
				roll = random.randrange(0, self.hats)
				id += self.encodeNum(roll)
			else: id += 'z'

			# Check that ID doesn't exist. If so, leave the loop
			if not exists(self.outputDir + id + '.png'):
				return id, bgColor
			else: print('| DUPE SLIME:', id)

	# Generates a slime
	def genSlime(self, id=None):
		# Check if an ID is given
		if not id:
			id, bg = self.genSlimeID()
		else:
			# Check if it already exists
			if exists(self.outputDir + id + '.png'):
				return None
			else:
				bg = self.decodeChar(id[1])

		layers = self.genSlimeLayers(id)
		return self.rollLayers(self.outputDir + id + '.png', layers, bg), id


	################
	# Bot Commands #
	################

	@commands.command(brief=desc['claim']['short'], description=desc['claim']['long'], aliases=desc['claim']['alias'])
	@commands.cooldown(1, 0, commands.BucketType.user)
	async def claim(self, ctx):
		# Check if the user is registered
		userID = str(ctx.author.id)
		self.checkUser(userID, ctx.author)

		# Get Payout
		ref = self.db.collection(self.collection).document(userID)
		payout, err = self.claimCoins(ref)

		if err != None:
			await ctx.reply(err, delete_after=10)
		else:
			coins = ref.get().to_dict()['coins']
			await ctx.reply(f'You collected **{payout}** coins! You now have **{coins}**.')

	@commands.command(brief=desc['generate']['short'], description=desc['generate']['long'], aliases=desc['generate']['alias'])
	@commands.cooldown(1, desc['generate']['cd'] * _cd, commands.BucketType.user)
	async def generate(self, ctx, count=1):
		userID = str(ctx.author.id)
		self.checkUser(userID, ctx.author)

		# Check if count is between 1 and 9
		if int(count) < 1 or int(count) > 9:
			await ctx.reply('You can only generate between 1 and 9 slimes at a time.', delete_after=5)
			return

		# Get user
		ref = self.db.collection(self.collection).document(userID)
		desc = ''

		# Check if user has enough coins
		coins = ref.get().to_dict()['coins']
		if coins < SLIME_PRICE * count:
			# Try to claim coins
			payout, err = self.claimCoins(ref)
			if err != None:
				# Get time left till next claim
				since = self.timeSince(ref.get().to_dict()['lastclaim'])
				mins, secs = self.convertTime(self.desc['claim']['cd'] - since)

				await ctx.reply(f'You need **{SLIME_PRICE * count - coins}** more coins! You can get more in *{mins}m, {secs}s*.', delete_after=10)
				return
			else:
				desc = f'You claimed {payout} coins!\n'
				coins = ref.get().to_dict()['coins']
		
		# Change count to the amount the user can afford
		if coins < SLIME_PRICE * count:
			count = int(coins / SLIME_PRICE)

		# Generate slimes
		slimes = []
		for i in range(int(count)):
			slimes.append(self.genSlime())

		# Add slime to the database
		for slime in slimes:
			ref.update({'slimes': firestore.ArrayUnion([slime[1]])})

		# Update balance
		ref.update({'coins': firestore.Increment(-SLIME_PRICE * count)})
		balance = f':coin: *{coins - SLIME_PRICE * count} left...*'

		# A single slime response
		if count == 1:
			slime = slimes[0]

			# Get rarity text
			rarityText = self.getRarity(slime[1])[0] + '\n\n'

			# Make embed and send it
			file = discord.File(slime[0])
			embed = discord.Embed(title=f'Generated **{slime[1]}**', description=rarityText + desc + balance, color=discord.Color.green())
			await ctx.reply(embed=embed, file=file)
		
		# Multiple slimes response
		else:
			# Make collage of generated slimes
			slimeIDs = [id for _, id in slimes]
			collage = self.makeCollage(userID, slimeIDs)

			# Make embed and send it
			file = discord.File(collage)
			embed = discord.Embed(title=f'Generated {count} slimes', description=desc + balance, color=discord.Color.green())
			await ctx.reply(embed=embed, file=file)
			os.remove(collage)

		# Upload slimes to firebase storage (Takes a second, better to do after response is given)
		bucket = storage.bucket()
		bucketPath = 'dev/' if _dev else 'prod/'
		for slime in slimes:
			blob = bucket.blob(f'{bucketPath}{slime[1]}.png')
			blob.upload_from_filename(slime[0])

	@commands.command(brief=desc['view']['short'], description=desc['view']['long'], aliases=desc['view']['alias'])
	@commands.cooldown(1, desc['view']['cd'] * _cd, commands.BucketType.user)
	async def view(self, ctx, id=None):
		# Check if given id is valid (incredibly insecure)
		if not id or len(id) != 8:
			await ctx.reply('I need a valid ID!', delete_after=5)
			return

		path = f'{self.outputDir}{id}.png'
		
		# Check if the slime exists
		if not exists(path):
			await ctx.reply(f'**{id}** doesn\'t exist!')
			return
		
		# Make embed and send it
		file = discord.File(path)
		await ctx.reply(file=file)

	@commands.command(brief=desc['inventory']['short'], description=desc['inventory']['long'], aliases=desc['inventory']['alias'])
	@commands.cooldown(1, desc['inventory']['cd'] * _cd, commands.BucketType.user)
	async def inventory(self, ctx, filter=None):
		perPage = 10
		username = str(ctx.author)[:str(ctx.author).rfind('#')]
		userID = str(ctx.author.id)
		self.checkUser(userID)
		buttons = ['??????', '??????', '??????', '??????']
		slimes = self.db.collection(self.collection).document(userID).get().to_dict()['slimes']

		# Check if user even has slimes
		if not slimes:
			await ctx.reply('You have no slimes!', delete_after=5)
			return

		# Filter slimes
		filtered = []
		if filter:
			if len(filter) == 8:
				for slime in slimes:
					if self.passesFilter(filter, slime):
						filtered.append(slime)
			else:
				await ctx.reply('Incorrect filter!', delete_after=5)
				return
		else:
			filtered = slimes

		# Check if there are any slimes that match the filter
		if not filtered:
			await ctx.reply('No slimes you own match that filter!', delete_after=5)
			return

		# Create the URL to the site
		siteAdd = self.siteLink + 'inventory/' + userID
		siteAdd = siteAdd + '?filter=' + filter if filter else siteAdd

		# Only post one page if less than listing amount
		if len(filtered) <= perPage:
			embed = embed=discord.Embed(title=f'{username}\'s Inventory', description=self.formatList(filtered, '\n'), url=siteAdd, color=discord.Color.green())
			embed.set_footer(text=f'{len(filtered)} slime(s)...')
			await ctx.reply(embed=embed)
			return

		# Put into pages of embeds
		pages = []
		numPages = math.ceil(len(filtered) / perPage)
		for i in range(numPages):
			# Slice array for page
			page = []
			max = ((i * perPage) + perPage) if (i != numPages - 1) else len(filtered)
			if i != numPages - 1:
				page = filtered[i * perPage:(i * perPage) + perPage]
			else:
				page = filtered[i * perPage:]
			# Setup pages embed
			embed=discord.Embed(title=f'{username}\'s Inventory', description=self.formatList(page, '\n'), url=siteAdd, color=discord.Color.green())
			embed.set_footer(text=f'Slimes {(i * perPage) + 1}-{max} of {len(filtered)}...')
			pages.append(embed)

		# Setup embed for reactions
		cur = 0
		msg = await ctx.reply(embed=pages[cur])
		for button in buttons:
			await msg.add_reaction(button)

		while True:
			try:
				reaction, _ = await self.bot.wait_for("reaction_add", check=lambda reaction, user: user == ctx.author and reaction.emoji in buttons, timeout=10.0)
			except asyncio.TimeoutError:
				return
			else:
				# Pick next page based on reaction
				prev = cur
				if reaction.emoji == buttons[0]:
					cur = 0
				if reaction.emoji == buttons[1]:
					if cur > 0:
						cur -= 1
				if reaction.emoji == buttons[2]:
					if cur < len(pages) - 1:
						cur += 1
				if reaction.emoji == buttons[3]:
					cur = len(pages) - 1
				for button in buttons:
					await msg.remove_reaction(button, ctx.author)
				if cur != prev:
					await msg.edit(embed=pages[cur])

	@commands.command(brief=desc['trade']['short'], description=desc['trade']['long'], aliases=desc['trade']['alias'])
	@commands.cooldown(1, desc['trade']['cd'] * _cd, commands.BucketType.user)
	@commands.guild_only()
	async def trade(self, ctx, other_person, your_slime, their_slime):
		# Remove whitespace from id and format arguments to make sense in s!help usage
		other = other_person.replace(' ', '')
		slime1 = your_slime
		slime2 = their_slime
		
		# Check if both users are registerd
		userID = str(ctx.author.id)
		otherID = other[3:-1]

		if userID == otherID:
			await ctx.reply('You can\t trade with yourself.', delete_after=5)
			return
		if not self.checkUser(userID) or not self.checkUser(otherID):
			await ctx.reply('You both need to be registered to trade!', delete_after=5)
			return

		# Basic check on given id's
		if len(slime1) != 8 or len(slime2) != 8:
			await ctx.reply('Given ID\'s need to be valid!', delete_after=5)
			return

		# Check if both users have slimes, including the ones referenced in args
		ref         = self.db.collection(self.collection).document(userID)
		otherRef    = self.db.collection(self.collection).document(otherID)
		slimes      = ref.get().to_dict()['slimes']
		otherSlimes = otherRef.get().to_dict()['slimes']
		if slime1 not in slimes:
			await ctx.reply(f'You don\'t own **{slime1}**!', delete_after=5)
			return
		if slime2 not in otherSlimes:
			await ctx.reply(f'They don\'t own **{slime2}**!', delete_after=5)
			return

		# Check if slimes are favorited:
		favs      = ref.get().to_dict()['favs']
		otherFavs = otherRef.get().to_dict()['favs']
		if slime1 in favs or slime2 in otherFavs:
			await ctx.reply('You can\'t trade favorited slimes!', delete_after=5)
			return

		# Make combined image
		s1img = Image.open(f'{self.outputDir}{slime1}.png')
		s2img = Image.open(f'{self.outputDir}{slime2}.png')
		exchangeImg = Image.open('./res/arrows.png')
		combined = Image.new(mode='RGBA', size=((self.width * 2) + 150, self.width), color=(0, 0, 0, 0))
		combined.paste(s1img, (0, 0))
		combined.paste(exchangeImg, (200, 0))
		combined.paste(s2img, (350, 0))
		fName = f'{self.outputDir}trade_{slime1}_{slime2}.png'
		# Place text
		font = ImageFont.truetype(self.fontPath, 20, encoding='unic')
		fontLen, _ = font.getsize('#' + slime1)
		draw = ImageDraw.Draw(combined)
		draw.text((self.width - fontLen, 0), f"#{slime1}", (0, 0, 0), font=font)
		draw.text((((self.width * 2) + 150) - fontLen, 0), f"#{slime2}", (0, 0, 0), font=font)
		# Save image
		combined.save(fName)
		combined.close()
		file = discord.File(fName)

		# Post trade request
		buttons = ['??????', '???']
		msg = await ctx.send(f'{other}: <@{userID}> wants to trade their **{slime1}** for your **{slime2}**. Do you accept?', file=file)
		os.remove(fName)
		for button in buttons:
			await msg.add_reaction(button)

		# Process message reaction
		try:
			reaction, user = await self.bot.wait_for("reaction_add", check=lambda reaction, user: user.id == int(otherID) and reaction.emoji in buttons, timeout=45.0)
		except asyncio.TimeoutError:
			return
		else:
			if reaction.emoji == buttons[0]:
				await ctx.send('The trade has been accepted!')

				# Add other persons slimes
				ref.update({'slimes': firestore.ArrayUnion([slime2])})
				otherRef.update({'slimes': firestore.ArrayUnion([slime1])})
				# Remove old slimes
				ref.update({'slimes': firestore.ArrayRemove([slime1])})
				otherRef.update({'slimes': firestore.ArrayRemove([slime2])})
				# Update trade message
				await msg.edit(content=f'The trade has been accepted!\n**{slime1}** :arrow_right: **{user}**\n**{slime2}** :arrow_right: **{ctx.author}**')
			elif reaction.emoji == buttons[1]:
				await ctx.send('The trade has been declined!')

	@commands.command(brief=desc['favorite']['short'], description=desc['favorite']['long'], aliases=desc['favorite']['alias'])
	@commands.cooldown(1, desc['favorite']['cd'] * _cd, commands.BucketType.user)
	async def favorite(self, ctx, id=None):
		# Check user is registered
		userID = str(ctx.author.id)
		if not self.checkUser(userID):
			await ctx.reply('You have no slimes!', delete_after=5)
			return

		# Grab users slimes
		ref = self.db.collection(self.collection).document(userID)
		slimes = ref.get().to_dict()['slimes']

		# Check if an id is provided or if they own it
		if not id:
			# Use most recently generated slime as id
			id = slimes[-1]
		elif id not in slimes:
			await ctx.reply('You don\'t own this slime!', delete_after=5)
			return

		res = self.favSlime(id, ref)
		await ctx.reply(res)

	@commands.command(brief=desc['favorites']['short'], description=desc['favorites']['long'], aliases=desc['favorites']['alias'])
	@commands.cooldown(1, desc['favorites']['cd'] * _cd, commands.BucketType.user)
	async def favorites(self, ctx, clear=None):
		# Check user is registered
		userID = str(ctx.author.id)
		if not self.checkUser(userID):
			await ctx.reply('You have no slimes!', delete_after=5)
			return

		# Check if they have any favs
		ref = self.db.collection(self.collection).document(userID)
		favs = ref.get().to_dict()['favs']
		if not favs:
			await ctx.reply('You don\'t have any favs!')
			return

		# Remove all favs from current user
		if clear in ['c', 'clear']:
			ref.update({'favs': []})
			await ctx.reply('Your favorites were reset.')
			return

		collage = self.makeCollage(userID, favs)
		file = discord.File(collage)
		await ctx.reply('Here are your favorites!', file=file)
		os.remove(collage)
	
	@commands.command(brief=desc['give']['short'], description=desc['give']['long'], aliases=desc['give']['alias'])
	@commands.cooldown(1, desc['give']['cd'] * _cd, commands.BucketType.user)
	@commands.is_owner()
	async def give(self, ctx, other, id):
		other.replace(' ', '')
		userID = other[2:-1]

		# Do basic checks
		if not self.checkUser(userID):
			await ctx.reply(f'**{userID}** needs to be registered.')
			return
		if len(id) != 8:
			await ctx.reply('ID\'s are 8 characters.')
			return
		
		# Generate slime and get id
		path, id = self.genSlime(id)

		if not path:
			await ctx.reply(f'**{id}** isn\'t a valid ID.')
			return

		# Add slime to the database
		ref = self.db.collection(self.collection).document(userID)
		ref.update({'slimes': firestore.ArrayUnion([id])})

		# Send slime to user
		file = discord.File(path)
		await ctx.reply(f'**{id}** was given to **{userID}**!', file=file)

		# Upload slime to firebase storage (Takes a second, better to do after response is given)
		bucket = storage.bucket()
		bucketPath = 'dev/' if _dev else 'prod/'
		blob = bucket.blob(f'{bucketPath}{id}.png')
		blob.upload_from_filename(path)

	@commands.command(brief=desc['rarity']['short'], description=desc['rarity']['long'], aliases=desc['rarity']['alias'])
	@commands.cooldown(1, desc['rarity']['cd'] * _cd, commands.BucketType.user)
	async def rarity(self, ctx, id):
		# Check if given id is valid
		if not id or len(id) != 8:
			await ctx.reply('I need a valid ID!', delete_after=5)
			return

		# Get data
		text, score = self.getRarity(id)

		# Send embed response
		embed = discord.Embed(title=f'{id}\' Rarity', description=text + f' (Score of {score})', color=discord.Color.green())
		await ctx.reply(embed=embed)

	@commands.command(brief=desc['rarities']['short'], description=desc['rarities']['long'], aliases=desc['rarities']['alias'])
	@commands.cooldown(1, desc['rarities']['cd'] * _cd, commands.BucketType.user)
	async def rarities(self, ctx):
		rarities = [
			'Extremely Ordinary',
			'Common',
			'Uncommon',
			'Rare',
			'Pretty Rare',
			'Very Rare',
			':sparkles: Overwhelmingly Rare',
		]

		embed = discord.Embed(title='Slime bRarities', description='\n'.join(rarities), color=discord.Color.green())
		await ctx.reply(embed=embed)

	@commands.command(brief=desc['top']['short'], description=desc['top']['long'], aliases=desc['top']['alias'])
	@commands.cooldown(1, desc['top']['cd'] * _cd, commands.BucketType.user)
	async def top(self, ctx, num=10):
		# Check user is registered
		userID = str(ctx.author.id)
		if not self.checkUser(userID):
			await ctx.reply('You have no slimes!', delete_after=5)
			return

		if num > 20:
			await ctx.reply('You can only check your top 20!', delete_after=5)
			return

		# Get data
		ref = self.db.collection(self.collection).document(userID)
		slimes = ref.get().to_dict()['slimes']
		rarities = [(self.getRarity(slime)[1], slime) for slime in slimes]
		rarities.sort(reverse=True)
		rarities = rarities[:num]

		# Send embed response
		embed = discord.Embed(title=f'{ctx.author.name}\'s Top {num} Slimes', color=discord.Color.green())
		for i, (score, slime) in enumerate(rarities):
			embed.add_field(name=f'#{i + 1}', value=f'{slime} (Score of {score})')
		await ctx.reply(embed=embed)

	@commands.command(brief=desc['sell']['short'], description=desc['sell']['long'], aliases=desc['sell']['alias'])
	@commands.cooldown(1, desc['sell']['cd'] * _cd, commands.BucketType.user)
	async def sell(self, ctx, id=None):
		# Check user is registered
		userID = str(ctx.author.id)
		if not self.checkUser(userID):
			await ctx.reply('You have no slimes to sell!', delete_after=5)
			return

		# Check if id is valid
		if id and len(id) != 8:
			await ctx.reply('I need a valid ID!', delete_after=5)
			return

		# Get users slimes
		ref = self.db.collection(self.collection).document(userID)
		user = ref.get().to_dict()
		slimes = user['slimes']
		coins = user['coins']
		favs = user['favs']

		# Check if user has slimes
		if not slimes:
			await ctx.reply('You have no slimes to sell!', delete_after=5)
			return

		# No id is provided
		if not id:
			# Select most recent slime if no id is given
			# Don't use favs
			idx = len(slimes) - 1
			while idx >= 0 and slimes[idx] in favs:
				idx -= 1
			id = slimes[idx]
		
		# They provide an id...
		else:
			# Check if they own it
			if id not in slimes:
				await ctx.reply('You don\'t own that slime!', delete_after=5)
				return
			# Check if its favorited
			if id in favs:
				await ctx.reply('You can\'t sell favorited slimes!', delete_after=5)
				return

		# Get slimes value
		value = math.ceil(self.getRarity(id)[1] * SELLING_RATIO)
		if value == 0: value = 1 # pity value

		# Build response
		buttons = ['??????', '???']
		path = f'{self.outputDir}{id}.png'
		file = discord.File(path)
		msg = await ctx.reply(f'Are you sure you want to sell **{id}** for {value} coin(s)?', file=file)
		for button in buttons: await msg.add_reaction(button)

		# Process response
		try:
			response, _ = await self.bot.wait_for('reaction_add', check=lambda reaction, user: user == ctx.author and reaction.emoji in buttons, timeout=10.0)
		except asyncio.TimeoutError:
			return
		else:
			# Sell the slime
			if response.emoji == buttons[0]:
				ref.update({'slimes': firestore.ArrayRemove([id])})
				ref.update({'coins': firestore.Increment(value)})
				s = 's' if value > 1 else ''
				await msg.edit(content=f'**{id}** was sold for {value} coin{s} (*New Balance: {coins + value}*)!')

				# Remove the image from the server
				os.remove(path)

			# Don't sell
			elif response.emoji == buttons[1]:
				await msg.edit(content='You turned away the offer.')

	@commands.command(brief=desc['balance']['short'], description=desc['balance']['long'], aliases=desc['balance']['alias'])
	@commands.cooldown(1, desc['balance']['cd'] * _cd, commands.BucketType.user)
	async def balance(self, ctx):
		# Check user is registered
		userID = str(ctx.author.id)
		if not self.checkUser(userID):
			await ctx.reply('You have no slimes!', delete_after=5)
			return
		
		# Get users coins
		coins = 0
		try:
			ref = self.db.collection(self.collection).document(userID)
			coins = ref.get().to_dict()['coins']
		except KeyError:
			ref.update({'coins': firestore.Increment(0)}) # Set users coins to 0 if they don't have the key in the db
		
		await ctx.reply(f'You have {coins} coin(s), that\'s worth like {round(coins / SLIME_PRICE, 1)} slime(s)!')


	@commands.command(brief=desc['profile']['short'], description=desc['profile']['long'], aliases=desc['profile']['alias'])
	@commands.cooldown(1, desc['profile']['cd'] * _cd, commands.BucketType.user)
	async def profile(self, ctx):
		# Check user is registered
		userID = str(ctx.author.id)
		if not self.checkUser(userID, ctx.author):
			await ctx.reply('You have no slimes!', delete_after=5)
			return
		
		# Get user data
		ref = self.db.collection(self.collection).document(userID)
		slimes = ref.get().to_dict()['slimes']
		coins = ref.get().to_dict()['coins']
		favs = ref.get().to_dict()['favs']

		# Loop through slimes to gather statistics
		totalValue = 0
		averageRarity = 0
		highestRarity = ('', 0)

		for slime in slimes:
			rarity = self.getRarity(slime)[1]
			if rarity > highestRarity[1]: highestRarity = (slime, rarity)
			averageRarity += rarity
			totalValue += rarity * SELLING_RATIO

		averageRarity /= len(slimes)
		averageRarity = round(averageRarity, 1)

		# Update tag in db
		ref.update({'tag': str(ctx.author)})

		# Build response
		embed = discord.Embed(title=f'{ctx.author.name}\'s Profile', color=discord.Color.green())
		embed.add_field(name='Total Slimes', value=f'{len(slimes)}')
		embed.add_field(name='Coins', value=f'{coins}')
		embed.add_field(name='Number of Favorites', value=f'{len(favs)}')
		embed.add_field(name='Total Value', value=f'{math.ceil(totalValue)} :coin:')
		embed.add_field(name='Average Rarity', value=f'{averageRarity}')
		embed.add_field(name='Rarest Slime', value=f'{highestRarity[0]} ({highestRarity[1]})')
		await ctx.reply(embed=embed)

	@commands.command(brief=desc['reset']['short'], description=desc['reset']['long'], aliases=desc['reset']['alias'])
	@commands.cooldown(1, desc['reset']['cd'] * _cd, commands.BucketType.user)
	async def reset(self, ctx):
		# Check user is registered
		userID = str(ctx.author.id)
		if not self.checkUser(userID):
			await ctx.reply('You have nothing to reset!', delete_after=5)
			return

		# Make confirmation method
		buttons = ['??????', '???']
		msg = await ctx.reply('Are you completely sure you want to reset your account? There are no reversals.')
		for button in buttons:
			await msg.add_reaction(button)

		# Process response
		try:
			reaction, _ = await self.bot.wait_for("reaction_add", check=lambda reaction, user: user == ctx.author and reaction.emoji in buttons, timeout=10.0)
		except asyncio.TimeoutError:
			return
		else:
			if reaction.emoji == buttons[0]:
				ref = self.db.collection(self.collection).document(userID)

				# Reset slimes stored on server
				slimes = ref.get().to_dict()['slimes']
				if slimes:
					allSlimes = os.listdir(self.outputDir)
					for slime in slimes:
						for f in allSlimes:
							if os.path.isfile(self.outputDir + f) and f[:f.rfind('.')] == slime:
								os.remove(self.outputDir + f)
				
				# Reset slimes stored in firebase storage
				# TODO

				# Remove user document in database and respond
				ref.delete()
				await msg.edit(content='Your account has been reset.')
			elif reaction.emoji == buttons[1]:
				await msg.edit(content='Your account is safe!')


def setup(bot):
	bot.add_cog(Slimes(bot))