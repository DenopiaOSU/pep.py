from collections import namedtuple
import json
import random
import re
import threading
from typing import Callable

import requests
import time
import sys
import pprint
import osupyparser

from discord_webhook import DiscordWebhook, DiscordEmbed
from common import generalUtils
from common.constants import mods
from logger import log
from common.ripple import userUtils
from constants import exceptions, slotStatuses, matchModModes, matchTeams, matchTeamTypes, matchScoringTypes
from common.constants import gameModes
from common.constants import privileges
from constants import serverPackets
from helpers import systemHelper
from objects import fokabot
from objects import glob
from config import conf
from helpers import chatHelper as chat
from datetime import datetime
from datetime import timedelta
from helpers.user_helper import username_safe
from helpers.status_helper import UserStatus

REGEX = "^{}( (.+)?)?$"
commands = {}

Command = namedtuple("Command", ["trigger", "callback", "syntax", "privileges"])
def registerCommand(trigger: str, syntax: str = None, privs: privileges = None):
	"""A decorator to set commands into list."""
	global commands
	def wrapper(handler: Callable) -> Callable:
		rgx = re.compile(REGEX.format(trigger))
		commands[rgx] = Command(
			trigger= trigger,
			callback= handler,
			syntax= syntax or "",
			privileges= privs or None
		)
		return handler
	return wrapper

# Change status things...
def refresh_bmap_cache(md5: str) -> None:
	"""Tells USSR to drop the beatmap cache for a specific beatmap."""

	glob.redis.publish("ussr:bmap_decache", md5)

def refresh_lb_cache(md5: str, mode: int, c_mode) -> None:
	"""Refreshes the leaderboard cache for a specific leaderboard."""

	data = f"{md5}:{mode}:{c_mode}"
	glob.redis.publish("ussr:lb_refresh", data)

def refresh_all_lbs(md5: str) -> None:
	"""Refreshes ALL of the leaderboards for a given beatmap."""

	for c_mode in (0, 1, 2):
		for mode in (0, 1, 2, 3): refresh_lb_cache(md5, mode, c_mode)

def calc_completion(bmapid, n300, n100, n50, miss):
	bmap = osupyparser.OsuFile(f"/opt/ainuuwu/pep.py/.data/maps/{bmapid}.osu").parse_file()

	total_hits = int(n300 + n100 + n50 + miss)

	obj_total = total_hits - 1
	n = len(bmap.hit_objects) - 1

	objs = []
	for p in bmap.hit_objects: objs.append(p.start_time)

	timing = int(objs[n]) - int(objs[0])
	p = int(objs[obj_total]) - int(objs[0])

	return (p / timing) * 100

def chimuMessage(beatmapID):
	beatmap = glob.db.fetch("SELECT song_name, beatmapset_id FROM beatmaps WHERE beatmap_id = %s LIMIT 1", [beatmapID])
	return "Download [https://chimu.moe/en/d/{} {}] from Chimu".format(
		beatmap["beatmapset_id"],
		"Unknown Beatmap" if beatmap is None else beatmap["song_name"],
	)

def beatconnectMessage(beatmapID):
	beatmap = glob.db.fetch("SELECT song_name, beatmapset_id FROM beatmaps WHERE beatmap_id = %s LIMIT 1", [beatmapID])
	return "Download [https://beatconnect.io/b/{} {}] from Beatconnect".format(
		beatmap["beatmapset_id"],
		"Unknown Beatmap" if beatmap is None else beatmap["song_name"],
	)
	
def mirrorMessage(beatmapID):
	beatmap = glob.db.fetch("SELECT song_name, beatmapset_id FROM beatmaps WHERE beatmap_id = %s LIMIT 1", [beatmapID])
	return "Download {} from [https://beatconnect.io/b/{} Beatconnect], [https://chimu.moe/en/d/{} Chimu] or [osu://dl/{} osu!direct].".format(
		"Unknown Beatmap" if beatmap is None else beatmap["song_name"],
		beatmap["beatmapset_id"],
		beatmap["beatmapset_id"],
		beatmap["beatmapset_id"],
	)

def restartShutdown(restart):
	"""Restart (if restart = True) or shutdown (if restart = False) pep.py safely"""
	msg = "We are performing some maintenance. Bancho will {} in 5 seconds. Thank you for your patience.".format("restart" if restart else "shutdown")
	systemHelper.scheduleShutdown(5, restart, msg)
	return msg

def getMatchIDFromChannel(chan: str):
	"""Gets the id from multiplayer channel."""

	if not chan.lower().startswith("#multi_"):
		return None

	raw_id = chan.lower().removeprefix("#multi_")
	if not raw_id.isdigit():
		return None

	match_id = int(raw_id)
	if match_id not in glob.matches.matches:
		return None

	return match_id

def getSpectatorHostUserIDFromChannel(chan):
	"""Gets user id from spectator channel."""
	if not chan.lower().startswith("#spect_"):
		return None

	raw_id = chan.lower().removeprefix("#spect_")
	if not raw_id.isdigit():
		return None

	user_id = int(raw_id)
	return user_id

def getPPMessage(userID, just_data = False):
	"""Display PP stats for a map."""
	try:
		# Get user token
		token = glob.tokens.getTokenFromUserID(userID)
		if token is None:
			return False

		currentMap = token.tillerino[0]
		currentMods = token.tillerino[1]
		currentAcc = token.tillerino[2]

		# Send request to LETS api
		url = f"http://localhost:2137/api/v1/pp?b={currentMap}&m={currentMods}"
		if currentAcc != -1:
			url += f"&a{currentAcc}"
		resp = requests.get(url, timeout=10)
		assert resp is not None
		data = json.loads(resp.text)

		# Make sure status is in response data
		if "status" not in data:
			raise exceptions.apiException("No data from API!")

		# Make sure status is 200
		if data["status"] != 200:
			if "message" in data:
				return f"There has been an exception the in PP API ({data['message']})."

		if just_data:
			return data

		# Format result.
		# Kisumi style :sunglasses:
		if currentAcc == -1:
			msg = (f"{data['song_name']} {'+' + generalUtils.readableMods(currentMods) if currentMods else ''}\n"
				f"| 100% = {data['pp'][0]:.2f}pp | | 99% = {data['pp'][1]:.2f}pp | | 98% = {data['pp'][2]:.2f}pp | | 95% = {data['pp'][3]:.2f}pp | ")
		else:
			msg = (f"{data['song_name']} {'+' + generalUtils.readableMods(currentMods) if currentMods else ''}\n"
				f"| {token.tillerino[2]:.2f}% = {data['pp'][0]:.2f}pp |")
		
		return msg
	except requests.exceptions.RequestException:
		# RequestException
		return "USSR API timeout. Please try again in a few seconds. If this doesnt work, the servers are burning down.. (help)"
	
"""
Commands callbacks

Must have fro, chan and messages as arguments
:param fro: username of who triggered the command
:param chan: channel"(or username, if PM) where the message was sent
:param message: list containing arguments passed from the message
				[0] = first argument
				[1] = second argument
				. . .

return the message or **False** if there's no response by the bot
TODO: Change False to None, because False doesn't make any sense
"""
@registerCommand(trigger='!map', privs=privileges.ADMIN_MANAGE_BEATMAPS, syntax='<rank/love/unrank> <set/map>')
def editMap(fro: str, chan: str, message: list[str]) -> str:
	"""Edit the ranked status of the last /np'ed map."""
	# Rank, unrank, and love maps with a single command.
	# Syntax: /np
	#         !map <rank/unrank/love> <set/map>
	message = [m.lower() for m in message]

	if not (token := glob.tokens.getTokenFromUsername(fro)):
		return

	if not token.tillerino[0]:
		return "Please give me a beatmap first with /np command."

	if message[0] not in {"rank", "unrank", "love"}:
		return "Status must be either rank, unrank, or love."

	if message[1] not in {"set", "map"}:
		return "Scope must either be set or map."

	statuses = {"love": 5, "rank": 2, "unrank": 0}
	stat_readable = {5: "loved", 2: "ranked", 0: "unranked"}

	status = statuses.get(message[0])
	status_readable = stat_readable.get(status)

	set_check = message[1] == "set"
	bmapset_or_bmap = "beatmapset_id" if set_check else "beatmap_id"

	res = glob.db.fetch(
		"SELECT ranked, beatmapset_id, song_name "
		"FROM beatmaps WHERE beatmap_id = %s",
		[token.tillerino[0]]
	)
	if not res:
		return "Could not find map.."

	if res["ranked"] == status:
		return f"That map is already {status_readable}!"

	rank_id = res["beatmapset_id"] if set_check else token.tillerino[0]

	# Update map's ranked status.
	glob.db.execute(
		"UPDATE beatmaps SET ranked = %s, ranked_status_freezed = 1, "
		f"rankedby = %s WHERE {bmapset_or_bmap} = %s",
		[status, token.userID, rank_id]
	)

	all_md5 = glob.db.fetchAll("SELECT beatmap_md5 FROM beatmaps WHERE beatmapset_id = %s", [res["beatmapset_id"]])
	for md5 in all_md5: refresh_all_lbs(md5['beatmap_md5'])

	if set_check: # In theory it should work, practically i have no fucking clue.
		map_name = res["song_name"].split("[")[0].strip()
		beatmap_url = f'the beatmap set [https://ussr.pl/beatmaps/{token.tillerino[0]} {map_name}]' 
	else:
		map_name = res["song_name"]
		beatmap_url = f'the beatmap [https://ussr.pl/beatmaps/{token.tillerino[0]} {map_name}]'

	if conf.NEW_RANKED_WEBHOOK:	
		webhook = DiscordWebhook(url=conf.NEW_RANKED_WEBHOOK)
		embed = DiscordEmbed(description=f"Ranked by {fro}", color=242424)
		embed.set_author(name=f"{map_name} was just {status_readable}", url=f"https://ussr.pl/beatmaps/{token.tillerino[0]}", icon_url=f"https://a.ussr.pl/{token.userID}")
		embed.set_footer(text="Enjoy!")
		embed.set_image(url=f"https://assets.ppy.sh/beatmaps/{res['beatmapset_id']}/covers/cover.jpg")
		webhook.add_embed(embed)
		webhook.execute()

	chat.sendMessage(glob.BOT_NAME, '#announce', f'[https://dolphinisretarded.xyz/u/{token.userID} {fro}] has {status_readable} {beatmap_url}')
	return f'Successfully {status_readable} a map.'

@registerCommand(trigger= "!ir", privs= privileges.ADMIN_MANAGE_SERVERS)
def instantRestart(fro, chan, message):
	"""Reloads pep.py instantly."""
	glob.streams.broadcast("main", serverPackets.notification("We are restarting Bancho. Be right back!"))
	systemHelper.scheduleShutdown(0, True, delay=5)
	return False

@registerCommand(trigger= "!roll")
def roll(fro, chan, message):
	"""Rolls a number between 0 and 100 (or provided number)."""
	maxPoints = 100
	if len(message) >= 1:
		if message[0].isdigit() and int(message[0]) > 0:
			maxPoints = int(message[0])

	points = random.randrange(0,maxPoints)
	return f"{fro} rolls {points} points!"

@registerCommand(trigger= "!alert", syntax= "<message>", privs= privileges.ADMIN_SEND_ALERTS)
def alert(fro, chan, message):
	"""Sends a notification to all currently online members."""
	msg = ' '.join(message[:]).strip()
	if not msg:
		return False
	glob.streams.broadcast("main", serverPackets.notification(msg))
	return False

@registerCommand(trigger= "!alertuser", syntax= "<username> <message>", privs= privileges.ADMIN_SEND_ALERTS)
def alertUser(fro, chan, message):
	"""Sends a notification to a specific user."""

	target = message[0].lower()
	targetToken = glob.tokens.getTokenFromUsername(username_safe(target), safe=True)
	if targetToken is not None:
		msg = ' '.join(message[1:]).strip()
		if not msg:
			return False
		targetToken.enqueue(serverPackets.notification(msg))
		return False
	else:
		return "User offline."

@registerCommand(trigger= "!moderated", privs= privileges.ADMIN_CHAT_MOD)
def moderated(fro, chan, message):
	try:
		# Make sure we are in a channel and not PM
		if not chan.startswith("#"):
			raise exceptions.moderatedPMException

		# Get on/off
		enable = True
		if len(message) >= 1:
			if message[0] == "off":
				enable = False

		# Turn on/off moderated mode
		glob.channels.channels[chan].moderated = enable
		return "This channel is {} in moderated mode!".format("now" if enable else "no longer")
	except exceptions.moderatedPMException:
		return "You are trying to put a private chat in moderated mode. Are you serious?!? You're fired."

@registerCommand(trigger= "!kickall", privs= privileges.ADMIN_MANAGE_SERVERS)
def kickAll(fro, chan, message):
	"""Kicks all members from the server (except staff)."""
	# Kick everyone but mods/admins
	toKick = []
	with glob.tokens:
		for key, value in glob.tokens.tokens.items():
			if not value.admin:
				toKick.append(key)

	# Loop though users to kick (we can't change dictionary size while iterating)
	for i in toKick:
		if i in glob.tokens.tokens:
			glob.tokens.tokens[i].kick()

	return "Whoops! Who needs players anyways?"

@registerCommand(trigger= "!kick", syntax= "<target>", privs= privileges.ADMIN_KICK_USERS)
def kick(fro, chan, message):
	"""Kicks a specific member from the server."""
	# Get parameters
	target = username_safe(" ".join(message))
	if target == glob.BOT_NAME.lower():
		return "Nope."

	# Get target token and make sure is connected
	tokens = glob.tokens.getTokenFromUsername(username_safe(target), safe=True, _all=True)
	if len(tokens) == 0:
		return f"{target} is not online"

	# Kick users
	for i in tokens:
		i.kick()

	# Bot response
	return f"{target} has been yeeted from bancho. Eh, oh well, no one liked {target} anyway though, right?"

@registerCommand(trigger= "!bot reconnect", privs= privileges.ADMIN_MANAGE_SERVERS)
def fokabotReconnect(fro, chan, message):
	"""Forces the bot to reconnect."""
	# Check if the bot is already connected
	if glob.tokens.getTokenFromUserID(999) is not None:
		return f"{glob.BOT_NAME} is already connected to Denopia!"

	# Bot is not connected, connect it
	fokabot.connect()
	return False

@registerCommand(trigger= "!bot reload", privs= privileges.ADMIN_MANAGE_SERVERS)
def reload_commands(fro, chan, mes) -> str:
	"""Reloads all of the RealistikBot commands."""

	fokabot.reload_commands()
	return "Miku has been reloaded successfully!"

@registerCommand(trigger= "!silence", syntax= "<target> <amount> <unit(s/m/h/d)> <reason>", privs= privileges.ADMIN_SILENCE_USERS)
def silence(fro, chan, message):
	"""Silences a specific user for a specific interval."""
	# message = [x.lower() for x in message]

	offset = 0
	for idx, line in message:
		if line.isdigit(): 
			offset = idx
			break
	
	target = username_safe(" ".join(message[:offset]))
	amount = message[offset]
	unit = message[offset+1].lower()
	reason = ' '.join(message[offset+2:]).strip()
	if not reason:
		return "Please provide a valid reason."
	if not amount.isdigit():
		return "The amount must be a number."

	# Get target user ID
	targetUserID = userUtils.getIDSafe(target)
	userID = userUtils.getID(fro)

	# Make sure the user exists
	if not targetUserID:
		return f"{target}: user not found"

	# Calculate silence seconds
	if unit == 's':
		silenceTime = int(amount)
	elif unit == 'm':
		silenceTime = int(amount) * 60
	elif unit == 'h':
		silenceTime = int(amount) * 3600
	elif unit == 'd':
		silenceTime = int(amount) * 86400
	else:
		return "Invalid time format (s/m/h/d)."

	# Max silence time is 7 days
	if silenceTime > 2.628e+6:
		return "Invalid silence time. Max silence time is 1 month."

	# Send silence packet to target if he's connected
	targetToken = glob.tokens.getTokenFromUsername(username_safe(target), safe=True)
	if targetToken is not None:
		# user online, silence both in db and with packet
		targetToken.silence(silenceTime, reason, userID)
	else:
		# User offline, silence user only in db
		userUtils.silence(targetUserID, silenceTime, reason, userID)

	# Log message
	msg = f"{target} has been silenced for: {reason}"
	return msg

@registerCommand(trigger= "!removesilence", syntax= "<target>", privs= privileges.ADMIN_SILENCE_USERS)
def removeSilence(fro, chan, message):
	"""Unsilences a specific user."""
	target = username_safe(" ".join(message))

	# Make sure the user exists
	targetUserID = userUtils.getIDSafe(target)
	userID = userUtils.getID(fro)
	if not targetUserID:
		return f"{target}: user not found"

	# Send new silence end packet to user if he's online
	targetToken = glob.tokens.getTokenFromUsername(username_safe(target), safe=True)
	if targetToken is not None:
		# User online, remove silence both in db and with packet
		targetToken.silence(0, "", userID)
	else:
		# user offline, remove islene ofnlt from db
		userUtils.silence(targetUserID, 0, "", userID)

	return f"{target}'s silence reset"

@registerCommand(trigger= "!ban", syntax= "<target>", privs= privileges.ADMIN_BAN_USERS)
def ban(fro, chan, message):
	"""Bans a specific user."""
	# Get parameters
	target = username_safe(" ".join(message))

	# Make sure the user exists
	targetUserID = userUtils.getIDSafe(target)
	userID = userUtils.getID(fro)
	if not targetUserID:
		return f"{target}: user not found"
	if targetUserID in (999, 1000, 1001, 1003):
		return "Cant ban the people who made the server, dipshit!"
	# Set allowed to 0
	userUtils.ban(targetUserID)

	# Send ban packet to the user if he's online
	targetToken = glob.tokens.getTokenFromUsername(username_safe(target), safe=True)
	if targetToken is not None:
		targetToken.enqueue(serverPackets.login_banned())

	log.rap(userID, f"has banned {target}", True)
	return f"RIP {target}. You will not be missed."

@registerCommand(trigger= "!unban", syntax= "<target>", privs= privileges.ADMIN_BAN_USERS)
def unban(fro, chan, message):
	"""Unans a specific user."""
	# Get parameters
	target = username_safe(" ".join(message))

	# Make sure the user exists
	targetUserID = userUtils.getIDSafe(target)
	userID = userUtils.getID(fro)
	if not targetUserID:
		return f"{target}: user not found"

	# Set allowed to 1
	userUtils.unban(targetUserID)

	log.rap(userID, f"has unbanned {target}", True)
	return f"Welcome back {target}!"

@registerCommand(trigger= "!restrict", syntax= "<target>", privs= privileges.ADMIN_BAN_USERS)
def restrict(fro, chan, message):
	"""Restricts a specific user."""
	# Get parameters
	target = username_safe(" ".join(message))

	# Make sure the user exists
	targetUserID = userUtils.getIDSafe(target)
	userID = userUtils.getID(fro)
	if not targetUserID:
		return f"{target}: user not found"
	if targetUserID in (999, 1000, 1001, 1003):
		return "Cant restrict the people who made the server, dipshit!"
		
	# Put this user in restricted mode
	userUtils.restrict(targetUserID)

	# Send restricted mode packet to this user if he's online
	targetToken = glob.tokens.getTokenFromUsername(username_safe(target), safe=True)
	if targetToken is not None:
		targetToken.notify_restricted()

	log.rap(userID, f"has put {target} in restricted mode", True)
	return f"Bye bye {target}. See you later, maybe."

@registerCommand(trigger= "!freeze", syntax= "<target>", privs= privileges.ADMIN_MANAGE_USERS)
def freeze(fro, chan, message):
	"""Freezes a specific user."""
	target = username_safe(" ".join(message))

	# Make sure the user exists
	targetUserID = userUtils.getIDSafe(target)
	userID = userUtils.getID(fro)
	if not targetUserID:
		return f"{target}: user not found"
	if targetUserID in (999, 1000, 1001, 1003):
		return "Cant freeze the people who made the server, dipshit!"

	# Get date & prepare freeze date
	now = datetime.now()
	freezedate = now + timedelta(days=2)
	freezedateunix = (freezedate-datetime(1970,1,1)).total_seconds()

	# Set freeze status & date
	glob.db.execute("UPDATE `users`  SET `frozen` = '1' WHERE `id` = '{}'".format(targetUserID))
	glob.db.execute("UPDATE `users`  SET `freezedate` = '{}' WHERE `id` = '{}'".format(freezedateunix, targetUserID))

	targetToken = glob.tokens.getTokenFromUsername(username_safe(target), safe=True)
	if targetToken is not None:
		targetToken.enqueue(serverPackets.notification("You have been frozen! The Denopia staff team has found you SUS and would like to request a liveplay. Visit denopia.ml for more info."))

	log.rap(userID, f"has frozen {target}", True)
	return "User has been frozen!"

@registerCommand(trigger= "!unfreeze", syntax= "<target>", privs= privileges.ADMIN_MANAGE_USERS)
def unfreeze(fro, chan, message):
	"""Unfreezes a specific user."""
	target = username_safe(" ".join(message))

	# Make sure the user exists
	targetUserID = userUtils.getIDSafe(target)
	userID = userUtils.getID(fro)
	if not targetUserID:
		return "{}: user not found".format(target)

	glob.db.execute("UPDATE `users`  SET `frozen` = '0' WHERE `id` = '{}'".format(targetUserID))
	glob.db.execute("UPDATE `users`  SET `freezedate` = '0' WHERE `id` = '{}'".format(targetUserID))
	glob.db.execute("UPDATE users  SET firstloginafterfrozen = '1' WHERE id = '{}'".format(targetUserID))
	#glob.db.execute(f"INSERT IGNORE INTO user_badges (user, badge) VALUES ({targetUserID}), 1005)")

	targetToken = glob.tokens.getTokenFromUsername(username_safe(target), safe=True)
	if targetToken is not None:
		targetToken.enqueue(serverPackets.notification("Your account has been unfrozen! You have proven your legitemacy. Thank you and have fun playing on Denopia!"))

	log.rap(userID, "has unfrozen {}".format(target), True)
	return "User has been unfrozen!"

@registerCommand(trigger= "!username", syntax= "<new username>", privs= privileges.USER_DONOR)
def changeUsername(fro, chan, message):
	"""Lets you change your username."""
	target = username_safe(fro)
	new = " ".join(message)
	newl = username_safe(new)

	targetUserID = userUtils.getIDSafe(target)

	if not targetUserID:
		return f"{target}: User not found"

	tokens = glob.tokens.getTokenFromUserID(targetUserID)
	glob.db.execute("UPDATE `users`  SET `username` = %s, `username_safe` = %s WHERE `id` = %s", (new, newl, targetUserID))
	glob.db.execute("UPDATE `users_stats` SET `username` = %s WHERE `id` = %s", (new, targetUserID))
	glob.db.execute("UPDATE `rx_stats` SET `username` = %s WHERE `id` = %s", (new, targetUserID))
	glob.db.execute("UPDATE `ap_stats` SET `username` = %s WHERE `id` = %s", (new, targetUserID))
	tokens[0].kick("Your username has been changed to {}. Please relog!".format(new))

@registerCommand(trigger= "!unrestrict", syntax= "<target>", privs= privileges.ADMIN_BAN_USERS)
def unrestrict(fro, chan, message):
	"""Unrestricts a specific user."""
	target = username_safe(" ".join(message))

	# Make sure the user exists
	targetUserID = userUtils.getIDSafe(target)
	userID = userUtils.getID(fro)
	if not targetUserID:
		return f"{target}: user not found"

	# Set allowed to 1
	userUtils.unrestrict(targetUserID)

	log.rap(userID, f"has removed restricted mode from {target}", True)
	return f"Welcome back {target}!"

@registerCommand(trigger= "!system restart", privs= privileges.ADMIN_MANAGE_SERVERS)
def systemRestart(fro, chan, message):
	return restartShutdown(True)

@registerCommand(trigger= "!system shutdown", privs= privileges.ADMIN_MANAGE_SERVERS)
def systemShutdown(fro, chan, message):
	return restartShutdown(False)

@registerCommand(trigger= "!system reload", privs= privileges.ADMIN_MANAGE_SERVERS)
def systemReload(fro, chan, message):
	glob.banchoConf.reload()
	return "Bancho settings reloaded!"

@registerCommand(trigger= "!system maintenance", privs= privileges.ADMIN_MANAGE_SERVERS)
def systemMaintenance(fro, chan, message):
	# Turn on/off bancho maintenance
	maintenance = True

	# Get on/off
	if len(message) >= 2:
		if message[1] == "off":
			maintenance = False

	# Set new maintenance value in bancho_settings table
	glob.banchoConf.setMaintenance(maintenance)

	if maintenance:
		# We have turned on maintenance mode
		# Users that will be disconnected
		who = []

		# Disconnect everyone but mod/admins
		with glob.tokens:
			for _, value in glob.tokens.tokens.items():
				if not value.admin:
					who.append(value.userID)

		glob.streams.broadcast("main", serverPackets.notification("Our realtime server is in maintenance mode. Please try to login again later."))
		glob.tokens.multipleEnqueue(serverPackets.login_error(), who)
		msg = "The server is now in maintenance mode!"
	else:
		# We have turned off maintenance mode
		# Send message if we have turned off maintenance mode
		msg = "The server is no longer in maintenance mode!"

	# Chat output
	return msg

@registerCommand(trigger= "!system status", privs= privileges.ADMIN_MANAGE_SERVERS)
def systemStatus(fro, chan, message):
	"""Shows the current server status."""
	# Fetch
	data = systemHelper.getSystemInfo()
	
	msg = "\n".join((
		"---> Denopia Server Statistics <---",
		" - Realtime Bancho Server -",
		"> Running Denopia's pep.py fork.",
		f"> Online Users: {str(data['connectedUsers'])}",
		f"> Multiplayer Matches: {str(data['matches'])}",
		f"> Uptime: {str(data['uptime'])}",
		"",
		" - System Statistics -",
		f"> CPU Utilisation: {str(data['cpuUsage'])}%",
		f"> RAM Utilisation: {str(data['usedMemory'])}/{str(data['totalMemory'])}",
	))

	return msg


@registerCommand(trigger= "\x01ACTION")
def tillerinoNp(fro, chan, message):
	"""Displays PP stats for a specific map."""
	# Mirror list trigger for #spect_
	if chan.startswith("#spect_"):
		spectatorHostUserID = getSpectatorHostUserIDFromChannel(chan)
		spectatorHostToken = glob.tokens.getTokenFromUserID(spectatorHostUserID)
		if spectatorHostToken is None:
			return False
		return mirrorMessage(spectatorHostToken.beatmapID)

	# Run the command in PM only
	if chan.startswith("#"):
		return False

	playWatch = message[1] in ("playing", "watching")
	# Get URL from message
	if message[1] == "listening":
		beatmapURL = str(message[3][1:])
	elif playWatch:
		beatmapURL = str(message[2][1:])
	else:
		return False

	modsEnum = 0
	mapping = {
		"-Easy": mods.EASY,
		"-NoFail": mods.NOFAIL,
		"+Hidden": mods.HIDDEN,
		"+HardRock": mods.HARDROCK,
		"+Nightcore": mods.NIGHTCORE,
		"+DoubleTime": mods.DOUBLETIME,
		"-HalfTime": mods.HALFTIME,
		"+Flashlight": mods.FLASHLIGHT,
		"-SpunOut": mods.SPUNOUT
	}

	if playWatch:
		for part in message:
			part = part.replace("\x01", "")
			modsEnum += mapping.get(part, 0)

	# Reject regex. Return to monkey.
	beatmapID = beatmapURL.split("/")[-1]
	
	# Sneaky peppy!! Changing URL!
	if "#" in beatmapID:
		_, beatmapID = beatmapID.split("#")

	# Update latest tillerino song for current token
	token = glob.tokens.getTokenFromUsername(fro)
	if token is not None:
		token.tillerino = [int(beatmapID), modsEnum, -1.0]
	userID = token.userID

	# Return tillerino message
	return getPPMessage(userID)

@registerCommand(trigger= "!with", syntax= "<mods>")
def tillerinoMods(fro, chan, message):
	"""Displays the PP stats for a specific map with specific mods."""
	# Run the command in PM only
	if chan.startswith("#"):
		return False

	# Get token and user ID
	token = glob.tokens.getTokenFromUsername(fro)
	if token is None:
		return False
	userID = token.userID

	# Make sure the user has triggered the bot with /np command
	if token.tillerino[0] == 0:
		return "You must firstly select a beatmap using the /np command."

	# Check passed mods and convert to enum
	modsList = [message[0][i:i+2].upper() for i in range(0, len(message[0]), 2)]
	modsEnum = 0
	for i in modsList:
		if i not in ["NO", "NF", "EZ", "HD", "HR", "DT", "HT", "NC", "FL", "SO", "RX", "AP"]:
			return "Invalid mods. Allowed mods: NO, NF, EZ, HD, HR, DT, HT, NC, FL, SO, RX, AP. Do not use spaces for multiple mods."
		
		modsInt = {
			"NO": 0,
			"NF": mods.NOFAIL,
			"EZ": mods.EASY,
			"HD": mods.HIDDEN,
			"HR": mods.HARDROCK,
			"DT": mods.DOUBLETIME,
			"HT": mods.HALFTIME,
			"NC": mods.NIGHTCORE,
			"FL": mods.FLASHLIGHT,
			"SO": mods.SPUNOUT,
			"RX": mods.RELAX,
			"AP": mods.RELAX2
		}.get(i, 0)

		modsEnum += modsInt
		if modsInt == 0:
			break

	# Set mods
	token.tillerino[1] = modsEnum

	# Return tillerino message for that beatmap with mods
	return getPPMessage(userID)

@registerCommand(trigger= "!acc", syntax= "<accuracy>")
def tillerinoAcc(fro, chan, message):
	"""Displays the PP stats for a specific map with a specific accuracy."""
	try:
		# Run the command in PM only
		if chan.startswith("#"):
			return False

		# Get token and user ID
		token = glob.tokens.getTokenFromUsername(fro)
		if token is None:
			return False
		userID = token.userID

		# Make sure the user has triggered the bot with /np command
		if token.tillerino[0] == 0:
			return "You must firstly select a beatmap using the /np command."

		# Convert acc to float
		acc = float(message[0])

		# Set new tillerino list acc value
		token.tillerino[2] = acc

		# Return tillerino message for that beatmap with mods
		return getPPMessage(userID)
	except ValueError:
		return "Invalid acc value"

@registerCommand(trigger= "!last")
def tillerinoLast(fro, chan, message):

	token = glob.tokens.getTokenFromUsername(fro)
	if token is None:
		return False

	table = ("scores_ap" if token.autopiloting else "scores_relax") if token.relaxing else "scores"

	data = glob.db.fetch(
		"""SELECT beatmaps.song_name as sn, {t}.*,
		beatmaps.beatmap_id as bid, beatmaps.max_combo as fc
		FROM {t} LEFT JOIN beatmaps ON beatmaps.beatmap_md5={t}.beatmap_md5
		LEFT JOIN users ON users.id = {t}.userid WHERE users.id = %s
		ORDER BY {t}.id DESC LIMIT 1""".format(t= table),
		[token.userID]
	)
	if not data:
		return "Please submit a score!"
	
	rank = generalUtils.getRank(
		data["play_mode"], data["mods"], data["accuracy"],
		data["300_count"], data["100_count"], data["50_count"], data["misses_count"]
	) if data["completed"] != 0 else 'F'

	
	fc_acc = generalUtils.calc_acc(data["play_mode"], data["300_count"] + data["misses_count"], data["100_count"], data["50_count"], 0, data["katus_count"], data["gekis_count"])
	token.tillerino[0] = data["bid"]
	token.tillerino[1] = data["mods"]
	token.tillerino[2] = fc_acc
	oppaiData = getPPMessage(token.userID, just_data=True)
	
	user_embed = f"[https://dolphinisretarded.xyz/u/{token.userID} {fro}]"
	map_embed = f"[http://ussr.pl/beatmaps/{data['bid']} {data['sn']}]"

	response = [f"{user_embed} | {map_embed} +{generalUtils.readableMods(data['mods'])}"]
	fc_or_failquit = (" (Failed/Quit)" if rank == "F" else " (Choke)") if not int(data['max_combo']) > int(int(data['fc']) * 0.95) and not data['misses_count'] == 0 else " (FC)"

	score_fced = (int(data['max_combo']) > int(int(data['fc']) * 0.95) and data['misses_count'] == 0 and rank != 'F')
	completion = calc_completion(data["bid"], data["300_count"], data["100_count"], data["50_count"], data["misses_count"])

	completion_or_pp = (f" | {completion:.2f}% map completed" if rank == "F" and data['play_mode'] == 0 else "") if score_fced else f" | ({oppaiData['pp'][-1]:.2f} for {fc_acc:.2f}% FC)" 
	accuracy_expanded = f"{data['100_count']}x100 // {data['50_count']}x50 // {data['misses_count']}xMiss"

	response.append(f"{{{rank.upper()}, {data['accuracy']:.2f}%}}{fc_or_failquit} {data['max_combo']}/{data['fc']}x | {data['pp']:.2f}pp | {oppaiData['stars']:.2f} ★{completion_or_pp}")
	response.append(f"{{ {accuracy_expanded} }}")

	return "\n".join(response)

reportRegex = re.compile("^(.+) \((.+)\)\:(?: )?(.+)?$")
@registerCommand(trigger= "!report")
def report(fro, chan, message):
	"""Reports a specific user."""
	msg = ""
	try:
		# TODO: Rate limit
		# Regex on message
		result = reportRegex.search(" ".join(message))

		# Make sure the message matches the regex
		if result is None:
			raise exceptions.invalidArgumentsException()

		# Get username, report reason and report info
		target, reason, additionalInfo = result.groups()
		target = username_safe(target)

		# Make sure the target is not foka
		if target.lower() == glob.BOT_NAME.lower():
			raise exceptions.invalidUserException()

		# Make sure the user exists
		targetID = userUtils.getID(target)
		if targetID == 0:
			raise exceptions.userNotFoundException()
		if targetUserID in (999, 1000, 1001, 1003):
			return "Cant report the people who made the server, dipshit!"

		# Make sure that the user has specified additional info if report reason is 'Other'
		if reason.lower() == "other" and additionalInfo is None:
			raise exceptions.missingReportInfoException()

		# Get the token if possible
		chatlog = ""
		token = glob.tokens.getTokenFromUsername(username_safe(target), safe=True)
		if token is not None:
			chatlog = token.getMessagesBufferString()

		# Everything is fine, submit report
		glob.db.execute("INSERT INTO reports (id, from_uid, to_uid, reason, chatlog, time) VALUES (NULL, %s, %s, %s, %s, %s)", [userUtils.getID(fro), targetID, "{reason} - ingame {info}".format(reason=reason, info="({})".format(additionalInfo) if additionalInfo is not None else ""), chatlog, int(time.time())])
		msg = (f"You've reported {target} for {reason} {(additionalInfo)}. A Community Manager will check your report as soon as possible. "
			"Every !report message you may see in chat wasn't sent to anyone, so nobody in chat, but admins, "
			"know about your report. Thank you for reporting!")
		adminMsg = f"{fro} has reported {target} for {reason} ({additionalInfo})"

		# Log report in #admin and on discord
		chat.sendMessage(glob.BOT_NAME, "#admin", adminMsg)
		log.warning(adminMsg, discord="cm")
	except exceptions.invalidUserException:
		msg = f"Hey, Miku here! You can't report me. I won't forget what you've tried to do. Watch out. 24th December at 12:27PM."
	except exceptions.invalidArgumentsException:
		msg = "Invalid report command syntax. To report an user, click on them and select 'Report user'."
	except exceptions.userNotFoundException:
		msg = "The user you've tried to report doesn't exist."
	except exceptions.missingReportInfoException:
		msg = "Please specify the reason of your report."
	finally:
		if msg != "":
			token = glob.tokens.getTokenFromUsername(fro)
			if token is not None:
				if token.irc:
					chat.sendMessage(glob.BOT_NAME, fro, msg)
				else:
					token.enqueue(serverPackets.notification(msg))
	return False

@registerCommand(trigger= "!mp", syntax= "<subcommand>", privs= privileges.USER_TOURNAMENT_STAFF)
def multiplayer(fro, chan, message):
	"""All the multiplayer subcommands."""
	def mpMake():
		if len(message) < 2:
			raise exceptions.invalidArgumentsException("Wrong syntax: !mp make <name>")
		matchName = " ".join(message[1:]).strip()
		if not matchName:
			raise exceptions.invalidArgumentsException("Match name must not be empty!")
		matchID = glob.matches.createMatch(matchName, generalUtils.stringMd5(generalUtils.randomString(32)), 0, "Tournament", "", 0, -1, isTourney=True)
		glob.matches.matches[matchID].sendUpdates()
		return f"Tourney match #{matchID} created!"

	def mpJoin():
		if len(message) < 2 or not message[1].isdigit():
			raise exceptions.invalidArgumentsException("Wrong syntax: !mp join <id>")
		matchID = int(message[1])
		userToken = glob.tokens.getTokenFromUsername(fro, ignoreIRC=True)
		if userToken is None:
			raise exceptions.invalidArgumentsException(
				"No game clients found for {}, can't join the match. "
				"If you're a referee and you want to join the chat "
				"channel from IRC, use /join #multi_{} instead.".format(fro, matchID)
			)
		userToken.joinMatch(matchID)
		return "Attempting to join match #{}!".format(matchID)

	def mpClose():
		matchID = getMatchIDFromChannel(chan)
		glob.matches.match_dispose(matchID)
		return "Multiplayer match #{} disposed successfully".format(matchID)

	def mpLock():
		matchID = getMatchIDFromChannel(chan)
		glob.matches.matches[matchID].isLocked = True
		return "This match has been locked"

	def mpUnlock():
		matchID = getMatchIDFromChannel(chan)
		glob.matches.matches[matchID].isLocked = False
		return "This match has been unlocked"

	def mpSize():
		if len(message) < 2 or not message[1].isdigit() or int(message[1]) < 2 or int(message[1]) > 16:
			raise exceptions.invalidArgumentsException("Wrong syntax: !mp size <slots(2-16)>")
		matchSize = int(message[1])
		_match = glob.matches.matches[getMatchIDFromChannel(chan)]
		_match.forceSize(matchSize)
		return "Match size changed to {}".format(matchSize)

	def mpMove():
		if len(message) < 3 or not message[2].isdigit() or int(message[2]) < 0 or int(message[2]) > 16:
			raise exceptions.invalidArgumentsException("Wrong syntax: !mp move <username> <slot>")
		username = message[1]
		newSlotID = int(message[2])
		userID = userUtils.getIDSafe(username)
		if userID is None:
			raise exceptions.userNotFoundException("No such user")
		_match = glob.matches.matches[getMatchIDFromChannel(chan)]
		success = _match.userChangeSlot(userID, newSlotID)
		if success:
			result = "Player {} moved to slot {}".format(username, newSlotID)
		else:
			result = "You can't use that slot: it's either already occupied by someone else or locked"
		return result

	def mpHost():
		if len(message) < 2:
			raise exceptions.invalidArgumentsException("Wrong syntax: !mp host <username>")
		username = message[1].strip()
		if not username:
			raise exceptions.invalidArgumentsException("Please provide a username")
		userID = userUtils.getIDSafe(username)
		if userID is None:
			raise exceptions.userNotFoundException("No such user")
		_match = glob.matches.matches[getMatchIDFromChannel(chan)]
		success = _match.setHost(userID)
		return "{} is now the host".format(username) if success else "Couldn't give host to {}".format(username)

	def mpClearHost():
		matchID = getMatchIDFromChannel(chan)
		glob.matches.matches[matchID].removeHost()
		return "Host has been removed from this match"

	def mpStart():
		def _start():
			matchID = getMatchIDFromChannel(chan)
			success = glob.matches.matches[matchID].start()
			if not success:
				chat.sendMessage(glob.BOT_NAME, chan, "Couldn't start match. Make sure there are enough players and "
												  "teams are valid. The match has been unlocked.")
			else:
				chat.sendMessage(glob.BOT_NAME, chan, "Have fun!")


		def _decreaseTimer(t):
			if t <= 0:
				_start()
			else:
				if t % 10 == 0 or t <= 5:
					chat.sendMessage(glob.BOT_NAME, chan, "Match starts in {} seconds.".format(t))
				threading.Timer(1.00, _decreaseTimer, [t - 1]).start()

		if len(message) < 2 or not message[1].isdigit():
			startTime = 0
		else:
			startTime = int(message[1])

		force = False if len(message) < 3 else message[2].lower() == "force"
		_match = glob.matches.matches[getMatchIDFromChannel(chan)]

		# Force everyone to ready
		someoneNotReady = False
		for i, slot in enumerate(_match.slots):
			if slot.status != slotStatuses.READY and slot.user is not None:
				someoneNotReady = True
				if force:
					_match.toggleSlotReady(i)

		if someoneNotReady and not force:
			return "Some users aren't ready yet. Use '!mp start force' if you want to start the match, " \
				   "even with non-ready players."

		if startTime == 0:
			_start()
			return "Starting match"
		else:
			_match.isStarting = True
			threading.Timer(1.00, _decreaseTimer, [startTime - 1]).start()
			return f"Match starts in {startTime} seconds. The match has been locked. " \
				   "Please don't leave the match during the countdown " \
				   "or you might receive a penalty."

	def mpInvite():
		if len(message) < 2:
			raise exceptions.invalidArgumentsException("Wrong syntax: !mp invite <username>")
		username = message[1].strip()
		if not username:
			raise exceptions.invalidArgumentsException("Please provide a username")
		userID = userUtils.getIDSafe(username)
		if userID is None:
			raise exceptions.userNotFoundException("No such user")
		token = glob.tokens.getTokenFromUserID(userID)
		if token is None:
			raise exceptions.invalidUserException("That user is not connected to bancho right now.")
		_match = glob.matches.matches[getMatchIDFromChannel(chan)]
		_match.invite(999, userID)
		token.enqueue(serverPackets.notification(f"Please accept the invite you've just received from {glob.BOT_NAME} to "
												 "enter your tourney match."))
		return f"An invite to this match has been sent to {username}"

	def mpMap():
		if len(message) < 2 or not message[1].isdigit() or (len(message) == 3 and not message[2].isdigit()):
			raise exceptions.invalidArgumentsException("Wrong syntax: !mp map <beatmapid> [<gamemode>]")
		beatmapID = int(message[1])
		gameMode = int(message[2]) if len(message) == 3 else 0
		if gameMode < 0 or gameMode > 3:
			raise exceptions.invalidArgumentsException("Gamemode must be 0, 1, 2 or 3")
		beatmapData = glob.db.fetch("SELECT * FROM beatmaps WHERE beatmap_id = %s LIMIT 1", [beatmapID])
		if beatmapData is None:
			raise exceptions.invalidArgumentsException("The beatmap you've selected couldn't be found in the database."
													   "If the beatmap id is valid, please load the scoreboard first in "
													   "order to cache it, then try again.")
		_match = glob.matches.matches[getMatchIDFromChannel(chan)]
		_match.beatmapID = beatmapID
		_match.beatmapName = beatmapData["song_name"]
		_match.beatmapMD5 = beatmapData["beatmap_md5"]
		_match.gameMode = gameMode
		_match.resetReady()
		_match.sendUpdates()
		return "Match map has been updated"

	def mpSet():
		if len(message) < 2 or not message[1].isdigit() or \
				(len(message) >= 3 and not message[2].isdigit()) or \
				(len(message) >= 4 and not message[3].isdigit()):
			raise exceptions.invalidArgumentsException("Wrong syntax: !mp set <teammode> [<scoremode>] [<size>]")
		_match = glob.matches.matches[getMatchIDFromChannel(chan)]
		matchTeamType = int(message[1])
		matchScoringType = int(message[2]) if len(message) >= 3 else _match.matchScoringType
		if not 0 <= matchTeamType <= 3:
			raise exceptions.invalidArgumentsException("Match team type must be between 0 and 3")
		if not 0 <= matchScoringType <= 3:
			raise exceptions.invalidArgumentsException("Match scoring type must be between 0 and 3")
		oldMatchTeamType = _match.matchTeamType
		_match.matchTeamType = matchTeamType
		_match.matchScoringType = matchScoringType
		if len(message) >= 4:
			_match.forceSize(int(message[3]))
		if _match.matchTeamType != oldMatchTeamType:
			_match.initializeTeams()
		if _match.matchTeamType == matchTeamTypes.TAG_COOP or _match.matchTeamType == matchTeamTypes.TAG_TEAM_VS:
			_match.matchModMode = matchModModes.NORMAL

		_match.sendUpdates()
		return "Match settings have been updated!"

	def mpAbort():
		_match = glob.matches.matches[getMatchIDFromChannel(chan)]
		_match.abort()
		return "Match aborted!"

	def mpKick():
		if len(message) < 2:
			raise exceptions.invalidArgumentsException("Wrong syntax: !mp kick <username>")
		username = message[1].strip()
		if not username:
			raise exceptions.invalidArgumentsException("Please provide a username")
		userID = userUtils.getIDSafe(username)
		if userID is None:
			raise exceptions.userNotFoundException("No such user")
		_match = glob.matches.matches[getMatchIDFromChannel(chan)]
		slotID = _match.getUserSlotID(userID)
		if slotID is None:
			raise exceptions.userNotFoundException("The specified user is not in this match")
		for i in range(0, 2):
			_match.toggleSlotLocked(slotID)
		return f"{username} has been kicked from the match."

	def mpPassword():
		password = "" if len(message) < 2 or not message[1].strip() else message[1]
		_match = glob.matches.matches[getMatchIDFromChannel(chan)]
		_match.changePassword(password)
		return "Match password has been changed!"

	def mpRandomPassword():
		password = generalUtils.stringMd5(generalUtils.randomString(32))
		_match = glob.matches.matches[getMatchIDFromChannel(chan)]
		_match.changePassword(password)
		return "Match password has been changed to a random one"

	def mpMods():
		if len(message) < 2:
			raise exceptions.invalidArgumentsException("Wrong syntax: !mp <mod1> [<mod2>] ...")
		_match = glob.matches.matches[getMatchIDFromChannel(chan)]
		newMods = 0
		freeMod = False
		for _mod in message[1:]:
			if _mod.lower().strip() == "hd":
				newMods |= mods.HIDDEN
			elif _mod.lower().strip() == "hr":
				newMods |= mods.HARDROCK
			elif _mod.lower().strip() == "dt":
				newMods |= mods.DOUBLETIME
			elif _mod.lower().strip() == "fl":
				newMods |= mods.FLASHLIGHT
			elif _mod.lower().strip() == "fi":
				newMods |= mods.FADEIN
			elif _mod.lower().strip() == "ez":
				newMods |= mods.EASY
			if _mod.lower().strip() == "none":
				newMods = 0

			if _mod.lower().strip() == "freemod":
				freeMod = True

		_match.matchModMode = matchModModes.FREE_MOD if freeMod else matchModModes.NORMAL
		_match.resetReady()
		if _match.matchModMode == matchModModes.FREE_MOD:
			_match.resetMods()
		_match.changeMods(newMods)
		return "Match mods have been updated!"

	def mpTeam():
		if len(message) < 3:
			raise exceptions.invalidArgumentsException("Wrong syntax: !mp team <username> <colour>")
		username = message[1].strip()
		if not username:
			raise exceptions.invalidArgumentsException("Please provide a username")
		colour = message[2].lower().strip()
		if colour not in ["red", "blue"]:
			raise exceptions.invalidArgumentsException("Team colour must be red or blue")
		userID = userUtils.getIDSafe(username)
		if userID is None:
			raise exceptions.userNotFoundException("No such user")
		_match = glob.matches.matches[getMatchIDFromChannel(chan)]
		_match.changeTeam(userID, matchTeams.BLUE if colour == "blue" else matchTeams.RED)
		return f"{username} is now in {colour} team"

	def mpSettings():
		_match = glob.matches.matches[getMatchIDFromChannel(chan)]
		single = False if len(message) < 2 else message[1].strip().lower() == "single"
		msg = "PLAYERS IN THIS MATCH "
		if not single:
			msg += "(use !mp settings single for a single-line version):"
			msg += "\n"
		else:
			msg += ": "
		empty = True
		for slot in _match.slots:
			if slot.user is None:
				continue
			readableStatuses = {
				slotStatuses.READY: "ready",
				slotStatuses.NOT_READY: "not ready",
				slotStatuses.NO_MAP: "no map",
				slotStatuses.PLAYING: "playing",
			}
			if slot.status not in readableStatuses:
				readableStatus = "???"
			else:
				readableStatus = readableStatuses[slot.status]
			empty = False
			msg += "* [{team}] <{status}> ~ {username}{mods}{nl}".format(
				team="red" if slot.team == matchTeams.RED else "blue" if slot.team == matchTeams.BLUE else "!! no team !!",
				status=readableStatus,
				username=glob.tokens.tokens[slot.user].username,
				mods=" (+ {})".format(generalUtils.readableMods(slot.mods)) if slot.mods > 0 else "",
				nl=" | " if single else "\n"
			)
		if empty:
			msg += "Nobody.\n"
		msg = msg.rstrip(" | " if single else "\n")
		return msg

	def mpScoreV():
		if len(message) < 2 or message[1] not in ("1", "2"):
			raise exceptions.invalidArgumentsException("Wrong syntax: !mp scorev <1|2>")
		_match = glob.matches.matches[getMatchIDFromChannel(chan)]
		_match.matchScoringType = matchScoringTypes.SCORE_V2 if message[1] == "2" else matchScoringTypes.SCORE
		_match.sendUpdates()
		return "Match scoring type set to scorev{}".format(message[1])

	def mpHelp():
		return "Supported subcommands: !mp <{}>".format("|".join(k for k in subcommands.keys()))

	try:
		subcommands = {
			"make": mpMake,
			"close": mpClose,
			"join": mpJoin,
			"lock": mpLock,
			"unlock": mpUnlock,
			"size": mpSize,
			"move": mpMove,
			"host": mpHost,
			"clearhost": mpClearHost,
			"start": mpStart,
			"invite": mpInvite,
			"map": mpMap,
			"set": mpSet,
			"abort": mpAbort,
			"kick": mpKick,
			"password": mpPassword,
			"randompassword": mpRandomPassword,
			"mods": mpMods,
			"team": mpTeam,
			"settings": mpSettings,
			"scorev": mpScoreV,
			"help": mpHelp
		}
		requestedSubcommand = message[0].lower().strip()
		if requestedSubcommand not in subcommands:
			raise exceptions.invalidArgumentsException("Invalid subcommand")
		return subcommands[requestedSubcommand]()
	except (exceptions.invalidArgumentsException, exceptions.userNotFoundException, exceptions.invalidUserException) as e:
		return str(e)
	except exceptions.wrongChannelException:
		return "This command only works in multiplayer chat channels"
	except exceptions.matchNotFoundException:
		return "Match not found"

@registerCommand(trigger= "!switchserver", syntax= "<server_url>", privs= privileges.ADMIN_MANAGE_SERVERS)
def switchServer(fro, chan, message):

	newServer = message[0].strip()
	if not newServer:
		return "Invalid server IP"
	targetUserID = userUtils.getIDSafe(fro)
	userID = userUtils.getID(fro)

	# Make sure the user exists
	if not targetUserID:
		return f"???????"

	# Connect the user to the end server
	userToken = glob.tokens.getTokenFromUserID(userID)
	userToken.enqueue(serverPackets.server_switch(newServer))

	# Disconnect the user from the origin server
	# userToken.kick()
	return f"You have been connected to {newServer}, We hoped you enjoyed your stay on Denopia!"

@registerCommand(trigger= "!announce", syntax= "<announcement>", privs= privileges.ADMIN_SEND_ALERTS)
def postAnnouncement(fro, chan, message): # Post to #announce ingame

	chat.sendMessage(glob.BOT_NAME, "#announce", ' '.join(message))
	return "Announcement successfully sent."

@registerCommand(trigger= "!chimu")
def chimu(fro, chan, message):
	"""Gets a download URL for the beatmap from Chimu."""
	user_id = getMatchIDFromChannel(chan)
	match_id = getSpectatorHostUserIDFromChannel(chan)

	if match_id:
		if match_id not in glob.matches.matches:
			return "This match doesn't seem to exist... Or does it...?"

		bmap_id = glob.matches.matches[match_id].beatmapID
	elif user_id:
		spec = glob.tokens.getTokenFromUserID(user_id)
		if not spec:
			return "The spectator host is offline."

		bmap_id = spec.beatmapID
	else:
		# Check for their tillerinio shit.
		token = glob.tokens.getTokenFromUsername(username_safe(fro), safe=True)
		if not token:
			return False # ??????????????????

		bmap_id = token.tillerino[0]
		if not bmap_id:
			return "You're currently not spectating or playing a match, if you wish to request beatmap mirror /np it before!"

	return chimuMessage(bmap_id)


@registerCommand(trigger= "!beatconnect")
def beatconnect(fro, chan, message):
	"""Gets a download URL for the beatmap from Beatconnect."""
	user_id = getMatchIDFromChannel(chan)
	match_id = getSpectatorHostUserIDFromChannel(chan)

	if match_id:
		if match_id not in glob.matches.matches:
			return "This match doesn't seem to exist... Or does it...?"

		bmap_id = glob.matches.matches[match_id].beatmapID
	elif user_id:
		spec = glob.tokens.getTokenFromUserID(user_id)
		if not spec:
			return "The spectator host is offline."

		bmap_id = spec.beatmapID
	else:
		# Check for their tillerinio shit.
		token = glob.tokens.getTokenFromUsername(username_safe(fro), safe=True)
		if not token:
			return False # ??????????????????

		bmap_id = token.tillerino[0]
		if not bmap_id:
			return "You're currently not spectating or playing a match, if you wish to request beatmap mirror /np it before!"

	return beatconnectMessage(bmap_id)

@registerCommand(trigger= "!mirror")
def mirror(fro, chan, message):
	"""Gets a download URL for the beatmap from various mirrors."""
	user_id = getMatchIDFromChannel(chan)
	match_id = getSpectatorHostUserIDFromChannel(chan)

	if match_id:
		if match_id not in glob.matches.matches:
			return "This match doesn't seem to exist... Or does it...?"

		bmap_id = glob.matches.matches[match_id].beatmapID
	elif user_id:
		spec = glob.tokens.getTokenFromUserID(user_id)
		if not spec:
			return "The spectator host is offline."

		bmap_id = spec.beatmapID
	else:
		# Check for their tillerinio shit.
		token = glob.tokens.getTokenFromUsername(username_safe(fro), safe=True)
		if not token:
			return False # ??????????????????

		bmap_id = token.tillerino[0]
		if not bmap_id:
			return "You're currently not spectating or playing a match, if you wish to request beatmap mirror /np it before!"

	return mirrorMessage(bmap_id)

@registerCommand(trigger= "!crash", syntax= "<target>", privs= privileges.ADMIN_MANAGE_USERS)
def crashuser(fro, chan, message):
	"""Crashes the persons game lmfao"""

	#talnacialex found this he is good lad
	target = message[0]
	targetToken = glob.tokens.getTokenFromUsername(username_safe(target), safe=True)
	if targetToken == None:
		#bruh they dont exist
		return "bruh they literally dont exist you fucking mentally retarded cunt"
	targetToken.enqueue(serverPackets.crash())
	return ":^)"

@registerCommand(trigger= "!bless", syntax= "<target>", privs= privileges.ADMIN_MANAGE_USERS)
def bless(fro: str, chan: str, message: str) -> str:
	"""Blesses them with the holy texts, and then proceeds to crash their game
	because the bible is chonky. Oh yeah this is also expensive CPU, Memory wise
	as there is a lot of packet writing and massive str."""

	target = username_safe(" ".join(message))
	t_user = glob.tokens.getTokenFromUsername(target, safe=True)
	if not t_user: return "This user is not online, and may not be blessed with the holy texts.."

	# Acquire bible from file.
	with open("bible.txt", "r") as stream:
		holy_bible = stream.read()
	
	# Split the bible into 2000 char chunks (str writer and reader limit)
	bible_split = [holy_bible [i:i+2000] for i in range(0, len(holy_bible), 2000)]
	
	# Use bytearray for speed
	q = bytearray()
	for b in bible_split:
		q += serverPackets.message_notify("Jesus", t_user.username, b)
	t_user.enqueue(q)
	return "THEY ARE BLESSED AND ASCENDED TO HeAVeN"

ASCII_TROLL = (
	"░░░░░▄▄▄▄▀▀▀▀▀▀▀▀▄▄▄▄▄▄░░░░░░░\n"
	"░░░░░█░░░░▒▒▒▒▒▒▒▒▒▒▒▒░░▀▀▄░░░░\n"
	"░░░░█░░░▒▒▒▒▒▒░░░░░░░░▒▒▒░░█░░░\n"
	"░░░█░░░░░░▄██▀▄▄░░░░░▄▄▄░░░░█░░\n"
	"░▄▀▒▄▄▄▒░█▀▀▀▀▄▄█░░░██▄▄█░░░░█░\n"
	"█░▒█▒▄░▀▄▄▄▀░░░░░░░░█░░░▒▒▒▒▒░█\n"
	"█░▒█░█▀▄▄░░░░░█▀░░░░▀▄░░▄▀▀▀▄▒█\n"
	"░█░▀▄░█▄░█▀▄▄░▀░▀▀░▄▄▀░░░░█░░█░\n"
	"░░█░░░▀▄▀█▄▄░█▀▀▀▄▄▄▄▀▀█▀██░█░░\n"
	"░░░█░░░░██░░▀█▄▄▄█▄▄█▄████░█░░░\n"
	"░░░░█░░░░▀▀▄░█░░░█░█▀██████░█░░\n"
	"░░░░░▀▄░░░░░▀▀▄▄▄█▄█▄█▄█▄▀░░█░░\n"
	"░░░░░░░▀▄▄░▒▒▒▒░░░░░░░░░░▒░░░█░\n"
	"░░░░░░░░░░▀▀▄▄░▒▒▒▒▒▒▒▒▒▒░░░░█░\n"
	"░░░░░░░░░░░░░░▀▄▄▄▄▄░░░░░░░░█░░\n"
)

@registerCommand(trigger="!trollnotif", syntax="<target>", privs=privileges.ADMIN_MANAGE_USERS)
def trollnotif(fro: str, chan: str, message: str) -> str:
	"""We do little bit of trolling :tf: (but this time through notifications)"""

	target = username_safe(" ".join(message))
	t_user = glob.tokens.getTokenFromUsername(target, safe=True)
	if not t_user: return "This user is not online, and may not be notification trolled."
	
	# Use bytearray for speed
	q = bytearray()
	q += serverPackets.notification(ASCII_TROLL)
	t_user.enqueue(q)
	return "They have been notification trolled :tf:"

@registerCommand(trigger= "!troll", syntax= "<target>", privs= privileges.ADMIN_MANAGE_USERS)
def troll(fro: str, chan: str, message: str) -> str:
	"""We do little bit of trolling :tf:"""

	target = username_safe(" ".join(message))
	t_user = glob.tokens.getTokenFromUsername(target, safe=True)
	if not t_user: return "This user is not online, and may not be trolled."
	
	# Use bytearray for speed
	q = bytearray()
	q += serverPackets.message_notify("Trollface", t_user.username, "We do little bit of trolling :tf:")
	q += serverPackets.message_notify("Trollface", t_user.username, ASCII_TROLL)
	t_user.enqueue(q)
	return "They have been trolled"

@registerCommand(trigger= "!py", syntax= "<code>", privs= privileges.ADMIN_MANAGE_USERS)
def py(fro: str, chan: str, message: str) -> str:
	"""Allows for code execution inside server (DANGEROUS COMMAND)"""

	user = glob.tokens.getTokenFromUsername(username_safe(fro), safe=True)
	if not user.userID in (1000, 1180):
		return "This command is reserved for head developers only!"

	if not message[0]:
		return "owo"

	definition = "\n ".join([f"def __py_{user.userID}():", " ".join(message)])

	try:  # def __py()
		exec(definition, glob.namespace)  # add to namespace
		ret = glob.namespace[f"__py_{user.userID}"]()
	except Exception as exc:  # return exception in osu! chat
		ret = pprint.pformat(f"{exc.__class__}: {exc}", compact=True)

	if f"__py_{user.userID}" in glob.namespace:
		del glob.namespace[f"__py_{user.userID}"]

	if ret is None:
		return "Success"

	if not isinstance(ret, str):
		ret = pprint.pformat(ret, compact=True)

	return ret

CMD_PER_PAGE = 5
@registerCommand(trigger= "!help")
def help_cmd(fro, chan, message):
	"""Lists all currently available commands!"""
	user = glob.tokens.getTokenFromUsername(fro)

	# Show them only commands they actually have access to lol.
	permed_list = []
	for cmd in commands.values():
		if (cmd.privileges and not \
			user.privileges & cmd.privileges) or cmd.trigger[0] != "!":
			continue
		permed_list.append(cmd)

	# Split the commands into blocks. PS gotta love this long line.
	cmd_blocks = [permed_list[i:i+CMD_PER_PAGE] for i in range(0, len(permed_list), CMD_PER_PAGE)]
	pages = len(cmd_blocks)

	index = 1
	if message and message[0].isdigit():
		if 1 <= int(message[0]) <= pages:
			index = int(message[0]) # Cursed.
		else:
			return f"Invalid page number (1-{pages})"

	help_cmd = []
	for idx, cmd in enumerate(cmd_blocks[index-1]): # In theory it should work

		# Make sure callback docstring is not none
		if not (docstr := cmd.callback.__doc__):
			docstr = "No description available."

		name = cmd.trigger
		if cmd.syntax: name += f" {cmd.syntax}"

		help_cmd.append(f"{1+idx+(CMD_PER_PAGE*(index-1))}. - {name} - {docstr}")

	header = [f"--- {index} of {pages} pages of commands currently available on Denopia's Miku! ---"]
	if index == 1:
		help_cmd.append("You can check syntax of individual command using !syntax <command eg. !help>")
	return "\n".join(header + help_cmd)

@registerCommand(trigger= "!syntax", syntax="<command>")
def syntax(fro, chan, message):
	"""Shows syntax of given command"""
	if not (token := glob.tokens.getTokenFromUsername(fro)):
		return False

	# Hardcode it as !help is **only** one command
	# that takes *optionals* variables.
	if message[0] == "!help":
		return "Syntax: !help <Optional: page number>"

	for _, cmd in commands.items():
		if cmd.trigger == " ".join(message):
			if cmd.privileges and not cmd.privileges & token.privileges:
				return False
			# Return it to them.
			return f"Syntax: {cmd.trigger} {cmd.syntax or '<No syntax>'}"

	return False

@registerCommand(trigger= "!status", syntax= "<status>")
def status_cmd(fro: str, chan: str, msg: list[str]) -> str:
	"""Sets a status for a user."""

	t_user = glob.tokens.getTokenFromUsername(fro)
	cur_status = glob.user_statuses.get_status(t_user.userID)
	msg_has_args = msg != [""] # I hate this.
	# They are toggling it.
	if (not msg_has_args) and cur_status:
		cur_status.enabled = not cur_status.enabled
		cur_status.insert()
		word = "on" if cur_status.enabled else "off"
		return f"Your status has been toggled {word}!"
	elif (not msg_has_args) and not cur_status:
		return (
			"You may not toggle your status if you do not have one! "
			"You may create a new one using the command !status <your status>"
		)
	new_status = " ".join(msg)

	if (st_len := len(new_status)) > 256:
		return f"This status is too long! (Max is 256, yours was {st_len})"

	status = UserStatus(
		id= None, # Set in insert.
		user_id= t_user.userID,
		status= new_status,
		enabled= True,
	)
	status.insert()

	return f"Your status has been set to: {new_status}"
