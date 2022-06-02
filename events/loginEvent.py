import sys
import time
import traceback

from common.constants import privileges
from logger import log
from common.ripple import userUtils
from constants import exceptions
from constants import serverPackets
from helpers import chatHelper as chat
from helpers import geo_helper
from objects import glob
from datetime import datetime
from helpers.realistik_stuff import Timer
from objects import glob
import random
from helpers.user_helper import verify_password
from helpers.geo_helper import get_full

UNFREEZE_NOTIF = serverPackets.notification(
	"Thank you for providing a liveplay! You have proven your legitemacy and "
	"have subsequently been unfrozen. Have fun playing on Denopia!"
)
FREEZE_RES_NOTIF = serverPackets.notification(
	"Your window for liveplay sumbission has expired! Your account has been "
	"restricted as per our cheating policy. Please contact staff for more "
	"information on what can be done. This can be done via the Denopia Discord"
	"server here: https://discord.gg/tukB8TZ42V"
)

def handle(tornadoRequest):
	# I wanna benchmark!
	t = Timer()
	t.start()
	# Data to return
	responseToken = None
	responseTokenString = ""
	responseData = bytearray()

	# Get IP from tornado request
	requestIP = tornadoRequest.getRequestIP()

	# Split POST body so we can get username/password/hardware data
	# 2:-3 thing is because requestData has some escape stuff that we don't need
	loginData = str(tornadoRequest.request.body)[2:-3].split("\\n")
	try:
		# Make sure loginData is valid
		if len(loginData) < 3:
			log.error("Login error (invalid login data)!")
			raise exceptions.invalidArgumentsException()

		# Get HWID, MAC address and more
		# Structure (new line = "|", already split)
		# [0] osu! version
		# [1] plain mac addressed, separated by "."
		# [2] mac addresses hash set
		# [3] unique ID
		# [4] disk ID
		splitData = loginData[2].split("|")
		osuVersion = splitData[0]
		timeOffset = int(splitData[1])
		clientData = splitData[3].split(":")[:5]
		if len(clientData) < 4:
			raise exceptions.forceUpdateException()

		# Try to get the ID from username
		username = str(loginData[0])
		safe_username = username.rstrip().replace(" ", "_").lower()

		# Set stuff from single query rather than many userUtils calls.
		user_db = glob.db.fetch(
			"SELECT id, privileges, silence_end, donor_expire, frozen, "
			"firstloginafterfrozen, freezedate FROM users "
			"WHERE username_safe = %s LIMIT 1",
			(safe_username,)
		)

		if not user_db:
			# Invalid username
			log.error(f"Login failed for user {username} (user not found)!")
			responseData += serverPackets.notification("Denopia: This user does not exist!")
			raise exceptions.loginFailedException()

		userID = user_db["id"]
		priv = int(user_db["privileges"])
		silence_end = int(user_db["silence_end"])
		donor_expire = int(user_db["donor_expire"])
		
		if not verify_password(userID, loginData[1]):
			# Invalid password
			log.error(f"Login failed for user {username} (invalid password)!")
			responseData += serverPackets.notification("Denopia: Invalid password!")
			raise exceptions.loginFailedException()

		# Make sure we are not banned or locked
		if (not priv & 3 > 0) and (not priv & privileges.USER_PENDING_VERIFICATION):
			log.error(f"Login failed for user {username} (user is banned)!")
			responseData += serverPackets.notification("Denopia: You have been banned! Stupid cheating fuck.")
			raise exceptions.loginBannedException()

		# No login errors!
		log.info(f"DB stuff and password verification done at {t.end_time_str()}")

		# Verify this user (if pending activation)
		firstLogin = False
		if priv & privileges.USER_PENDING_VERIFICATION or not userUtils.hasVerifiedHardware(userID):
			if userUtils.verifyUser(userID, clientData):
				# Valid account
				log.info(f"Account {userID} verified successfully!")
				glob.verifiedCache[str(userID)] = 1
				firstLogin = True
			else:
				# Multiaccount detected
				log.info(f"Account {userID} NOT verified!")
				glob.verifiedCache[str(userID)] = 0
				raise exceptions.loginBannedException()


		# Save HWID in db for multiaccount detection
		hwAllowed = userUtils.logHardware(userID, clientData, firstLogin) # THIS IS SO SLOW

		# This is false only if HWID is empty
		# if HWID is banned, we get restricted so there's no
		# need to deny bancho access
		if not hwAllowed:
			raise exceptions.haxException()

		# Log user IP
		userUtils.logIP(userID, requestIP)

		# Log user osuver
		glob.db.execute("UPDATE users SET osuver = %s WHERE id = %s LIMIT 1", [osuVersion, userID])
		log.info(f"Finished hardware and logging IP at {t.end_time_str()}")

		# Delete old tokens for that user and generate a new one
		isTournament = "tourney" in osuVersion
		if not isTournament:
			glob.tokens.deleteOldTokens(userID)
		responseToken = glob.tokens.addToken(userID, requestIP, timeOffset=timeOffset, tournament=isTournament)
		responseTokenString = responseToken.token

		# Check restricted mode (and eventually send message)
		# Cache this for less db queries
		user_restricted = (priv & privileges.USER_NORMAL) and not (priv & privileges.USER_PUBLIC)

		if user_restricted: responseToken.notify_restricted()
		#responseToken.checkRestricted()

		# Check if frozen
		frozen = user_db["frozen"]

		present = datetime.now()
		readabledate = datetime.utcfromtimestamp(user_db["freezedate"]).strftime('%d-%m-%Y %H:%M:%S')
		date2 = datetime.utcfromtimestamp(user_db["freezedate"]).strftime('%d/%m/%Y')
		date3 = present.strftime('%d/%m/%Y')
		passed = date2 < date3
		if frozen and not passed:
				responseToken.enqueue(serverPackets.notification(f"The Denopia staff team has found your plays suspicious and would like to request a liveplay. You have until {readabledate} (UTC) to provide a liveplay to the staff team. This can be done via the Denopia Discord server. Failure to provide a valid liveplay will result in your account being automatically restricted."))
		elif frozen and passed:
				responseToken.enqueue(FREEZE_RES_NOTIF)
				userUtils.restrict(responseToken.userID)

		#we thank unfrozen people		
		if not frozen and user_db["firstloginafterfrozen"]:
			responseToken.enqueue(UNFREEZE_NOTIF)
			glob.db.execute(f"UPDATE users SET firstloginafterfrozen = 0 WHERE id = {userID}")

		# Send message if donor expires soon
		if responseToken.privileges & privileges.USER_DONOR:
			if donor_expire-int(time.time()) <= 86400*3:
				expireDays = round((donor_expire-int(time.time()))/86400)
				expireIn = "{} days".format(expireDays) if expireDays > 1 else "less than 24 hours"
				responseToken.enqueue(serverPackets.notification("Your supporter status expires in {}! Following this, you will lose your supporter privileges (such as the further profile customisation options, name changes or profile wipes) and will not be able to access supporter features. If you wish to keep supporting Denopia and you don't want to lose your donor privileges, you can donate again by clicking on 'Donate' on our website. (being worked on currently..)".format(expireIn)))

		# Get only silence remaining seconds
		responseToken.silenceEndTime = silence_end
		silenceSeconds = responseToken.getSilenceSecondsLeft()
		# Get supporter/GMT
		userGMT = False
		userSupporter = not user_restricted
		userTournament = False
		userGMT = responseToken.admin
		userTournament = bool(responseToken.privileges & privileges.USER_TOURNAMENT_STAFF)

		# Server restarting check
		if glob.restarting: raise exceptions.banchoRestartingException()

		# Maintenance check
		if glob.banchoConf.config["banchoMaintenance"]:
			if not userGMT:
				# We are not mod/admin, delete token, send notification and logout
				glob.tokens.deleteToken(responseTokenString)
				raise exceptions.banchoMaintenanceException()
			else:
				# We are mod/admin, send warning notification and continue
				responseToken.enqueue(serverPackets.notification("Bancho is in maintenance mode. Only mods/admins have full access to the server.\nType !system maintenance off in chat to turn off maintenance mode."))
		
		log.info(f"Donor, silence and maintenence checks at {t.end_time_str()}")

		# BAN CUSTOM CHEAT CLIENTS
		# 0Ainu = First Ainu build
		# b20190326.2 = Ainu build 2 (MPGH PAGE 10)
		# b20190401.22f56c084ba339eefd9c7ca4335e246f80 = Ainu Aoba's Birthday Build
		# b20191223.3 = Unknown Ainu build? (Taken from most users osuver in cookiezi.pw)
		# b20190226.2 = hqOsu (hq-af)

		# TODO: Rewrite this mess
		# Ainu Client 2020 update
		if tornadoRequest.request.headers.get("ainu"):
			log.info(f"Account {userID} tried to use Ainu Client 2020!")
			if user_restricted:
				responseToken.enqueue(serverPackets.notification("Nice try you fucking cactus lookin lil bitch."))
			else:
				glob.tokens.deleteToken(userID)
				userUtils.restrict(userID)
				userUtils.appendNotes(userID, "User restricted on login for Ainu Client 2020.")
				raise exceptions.loginCheatClientsException()
		# Ainu Client 2019
		elif osuVersion in ("0Ainu", "b20190326.2", "b20190401.22f56c084ba339eefd9c7ca4335e246f80", "b20191223.3"):
			log.info(f"Account {userID} tried to use Ainu Client!")
			if user_restricted:
				responseToken.enqueue(serverPackets.notification("Nice try you fucking cactus lookin lil bitch."))
			else:
				glob.tokens.deleteToken(userID)
				userUtils.restrict(userID)
				userUtils.appendNotes(userID, "User restricted on login for Ainu Client 2019 (or older).")
				raise exceptions.loginCheatClientsException()
		# hqOsu
		elif osuVersion == "b20190226.2":
			log.info(f"Account {userID} tried to use hqOsu!")
			if user_restricted:
				responseToken.enqueue(serverPackets.notification("Comedian."))
			else:
				glob.tokens.deleteToken(userID)
				userUtils.restrict(userID)
				userUtils.appendNotes(userID, "User restricted on login for HQOsu (normal).")
				raise exceptions.loginCheatClientsException()
		
		#hqosu legacy
		elif osuVersion == "b20190716.5":
			log.info(f"Account {userID} tried to use hqOsu legacy!")
			if user_restricted:
				responseToken.enqueue(serverPackets.notification("Comedian."))
			else:
				glob.tokens.deleteToken(userID)
				userUtils.restrict(userID)
				userUtils.appendNotes(userID, "User restricted on login for HQOsu (legacy).")
				raise exceptions.loginCheatClientsException()
		# Budget Hacked client.
		elif osuVersion.startswith("skoot"):
			if user_restricted: responseToken.enqueue(serverPackets.notification("why tf you using 2016 scooter client lmfao."))
			else:
				glob.tokens.deleteToken(userID)
				userUtils.restrict(userID)
				userUtils.appendNotes(userID, "Wack 2016 Scooter client.")
				raise exceptions.loginCheatClientsException()
		
		# Blanket cover for most retard clients, force update.
		elif osuVersion[0] != "b":
			glob.tokens.deleteToken(userID)
			raise exceptions.haxException()
		
		log.info(f"Anticheat checks at {t.end_time_str()}")

		# Send all needed login packets
		responseToken.enqueue(
			bytearray(serverPackets.silence_end_notify(silenceSeconds)) + # Fast addition
			serverPackets.login_reply(userID) +
			serverPackets.protocol_version() +
			serverPackets.bancho_priv(userSupporter, userGMT, userTournament) +
			serverPackets.user_presence(userID, True) +
			serverPackets.user_stats(userID) +
			serverPackets.channel_info_end() +
			serverPackets.friend_list(userID)
		)

		# Default opened channels
		# TODO: Configurable default channels
		chat.joinChannel(token=responseToken, channel="#osu")
		chat.joinChannel(token=responseToken, channel="#announce")

		# Join admin channel if we are an admin
		if responseToken.admin:
			chat.joinChannel(token=responseToken, channel="#admin")

		# Output channels info
		for key, value in glob.channels.channels.items():
			if value.publicRead and not value.hidden:
				responseToken.enqueue(serverPackets.channel_info(key))

		# Send main menu icon
		if glob.banchoConf.config["menuIcon"] != "":
			responseToken.enqueue(serverPackets.menu_icon(glob.banchoConf.config["menuIcon"]))

		# Send online users' panels
		with glob.tokens:
			for token in glob.tokens.tokens.values():
				if not token.restricted:
					responseToken.enqueue(serverPackets.user_presence(token.userID))
		
		log.info(f"Server state and chat {t.end_time_str()}")

		# Localise the user based off IP.
		# Get location and country from IP
		latitude, longitude, countryLetters = get_full(requestIP)

		country = geo_helper.getCountryID(countryLetters)

		# Set location and country
		responseToken.setLocation(latitude, longitude)
		responseToken.country = country

		# Set country in db if user has no country (first bancho login)
		if userUtils.getCountry(userID) == "XX": userUtils.setCountry(userID, countryLetters)

		# Send to everyone our userpanel if we are not restricted or tournament
		if not responseToken.restricted:
			glob.streams.broadcast("main", serverPackets.user_presence(userID))
		
		#creating notification
		t_str = t.end_time_str()
		online_users = len(glob.tokens.tokens)
		# Wylie has his own quote he gets to enjoy only himself lmfao. UPDATE: Electro gets it too.
		if userID == 1000: quote = "I made the KKK in OSU!"
		# Ced also gets his own AS HE DOESNT WANT TO CHECK FAST SPEED.
		elif userID == 1001: quote = "WoRk On HaNaYo DiPsHiT - koenji"
		else: quote = random.choice(glob.banchoConf.config['Quotes'])
		notif = f"""- Online Users: {online_users}\n- {quote}"""
		if responseToken.admin: notif += f"\n- Elapsed: {t_str}!"
		responseToken.enqueue(serverPackets.notification(notif))
		
		log.info(f"Authentication attempt took {t_str}!")

		# Set reponse data to right value and reset our queue
		responseData = responseToken.fetch_queue()
	except exceptions.loginFailedException:
		# Login failed error packet
		# (we don't use enqueue because we don't have a token since login has failed)
		responseData += serverPackets.login_failed()
	except exceptions.invalidArgumentsException:
		# Invalid POST data
		# (we don't use enqueue because we don't have a token since login has failed)
		responseData += serverPackets.login_failed()
		responseData += serverPackets.notification("I have eyes y'know? (dont try that again, i'm always watching... ErrorCode: INVPODATA)")
	except exceptions.loginBannedException:
		# Login banned error packet
		responseData += serverPackets.login_banned()
	except exceptions.loginLockedException:
		# Login banned error packet
		responseData += serverPackets.login_locked()
	except exceptions.loginCheatClientsException:
		# Banned for logging in with cheats
		responseData += serverPackets.login_cheats()
	except exceptions.banchoMaintenanceException:
		# Bancho is in maintenance mode
		responseData = bytes()
		if responseToken is not None:
			responseData = responseToken.fetch_queue()
		responseData += serverPackets.notification("Bancho server is under maintenance. Please check our discord server for updates on this.")
		responseData += serverPackets.login_failed()
	except exceptions.banchoRestartingException:
		# Bancho is restarting
		responseData += serverPackets.notification("Bancho is restarting. Try again in a minute.")
		responseData += serverPackets.login_failed()
	except exceptions.need2FAException:
		# User tried to log in from unknown IP
		responseData += serverPackets.verification_required()
	except exceptions.haxException:
		# Using oldoldold client, we don't have client data. Force update.
		# (we don't use enqueue because we don't have a token since login has failed)
		responseData += serverPackets.force_update()
		responseData += serverPackets.notification("You really be using that old of a client? Fuckin hell man, force updating yo bitch ass ash bones client the fuck bro")
	except:
		log.error("Unknown error!\n```\n{}\n{}```".format(sys.exc_info(), traceback.format_exc()))
	finally:
		# Console and discord log
		if len(loginData) < 3:
			log.info("Invalid bancho login request from **{}** (insufficient POST data)".format(requestIP), "bunker")

		# Return token string and data
		return responseTokenString, bytes(responseData)
