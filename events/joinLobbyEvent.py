from logger import log
from constants import serverPackets
from objects import glob


def handle(userToken, _):
	# Get userToken data
	username = userToken.username

	# Add user to users in lobby
	userToken.joinStream("lobby")

	# Send matches data
	for key, _ in glob.matches.matches.items():
		userToken.enqueue(serverPackets.match_create(key))

	# Console output
	log.info("{} has joined multiplayer lobby".format(username))
