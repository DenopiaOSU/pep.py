from common.constants import actions
from common.log import logUtils as log
from common.ripple import userUtils
from constants import clientPackets
from constants import serverPackets
from objects import glob


def handle(userToken, packetData):
	# Get usertoken data
	userID = userToken.userID
	username = userToken.username

	# Update privileges
	userToken.updatePrivileges()

	# Make sure we are not banned
	if userUtils.isBanned(priv=userToken.privileges):
		userToken.enqueue(serverPackets.loginBanned())
		return

	# Send restricted message if needed
	if not userToken.restricted:
		if userUtils.isRestricted(priv=userToken.privileges):
			userToken.setRestricted()

	# Change action packet
	packetData = clientPackets.userActionChange(packetData)

	# If we are not in spectate status but we're spectating someone, stop spectating
	'''
if userToken.spectating != 0 and userToken.actionID != actions.WATCHING and userToken.actionID != actions.IDLE and userToken.actionID != actions.AFK:
	userToken.stopSpectating()

# If we are not in multiplayer but we are in a match, part match
if userToken.matchID != -1 and userToken.actionID != actions.MULTIPLAYING and userToken.actionID != actions.MULTIPLAYER and userToken.actionID != actions.AFK:
	userToken.partMatch()
		'''

	# Update cached stats if our pp changed if we've just submitted a score or we've changed gameMode
	if (userToken.actionID == actions.PLAYING or userToken.actionID == actions.MULTIPLAYING) or (userToken.pp != userUtils.getPP(userID, userToken.gameMode)) or (userToken.gameMode != packetData["gameMode"]):
		# Always update game mode, or we'll cache stats from the wrong game mode if we've changed it
		userToken.gameMode = packetData["gameMode"]
		userToken.updateCachedStats()

	# Always update action id, text, md5 and beatmapID
	userToken.actionID = packetData["actionID"]
	userToken.actionText = packetData["actionText"]
	userToken.actionMd5 = packetData["actionMd5"]
	userToken.actionMods = packetData["actionMods"]
	userToken.beatmapID = packetData["beatmapID"]

	# Enqueue our new user panel and stats to us and our spectators
	recipients = [userToken]
	if len(userToken.spectators) > 0:
		for i in userToken.spectators:
			if i in glob.tokens.tokens:
				recipients.append(glob.tokens.tokens[i])

	for i in recipients:
		if i is not None:
			# Force our own packet
			force = True if i == userToken else False
			i.enqueue(serverPackets.userPanel(userID, force))
			i.enqueue(serverPackets.userStats(userID, force))

	# Console output
	log.info("{} changed action: {} [{}][{}][{}]".format(username, str(userToken.actionID), userToken.actionText, userToken.actionMd5, userToken.beatmapID))
