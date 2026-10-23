UPLOAD_SPEED ?= 921600
# Board options differ per chip, so each one carries its own FQBN. The S3's
# Macronix flash reads back 0xff once the QIO bootloader takes it out of DIO,
# and the partition table is the first thing it fails to find, so it boots only
# on the DIO bootloader.
FQBN_esp32   = esp32:esp32:esp32:PartitionScheme=min_spiffs,UploadSpeed=$(UPLOAD_SPEED)
FQBN_esp32s3 = esp32:esp32:esp32s3:PartitionScheme=min_spiffs,FlashMode=dio,UploadSpeed=$(UPLOAD_SPEED)
# The C3 SuperMini has no UART bridge, so Serial reaches a console only by going
# out the chip's own USB.
FQBN_esp32c3 = esp32:esp32:esp32c3:PartitionScheme=min_spiffs,CDCOnBoot=cdc,UploadSpeed=$(UPLOAD_SPEED)
# A chip with no FQBN here has never run this firmware, and guessing its flash
# layout costs a board that boot-loops instead of a message.
board_fqbn = $(or $(FQBN_$(1)),$(error no FQBN for '$(1)' — add FQBN_$(1) to the Makefile))
# Compiling picks a target; uploading has to match the board on the other end of
# the cable, which is why only upload reads the chip.
BOARD ?= esp32
FQBN   = $(call board_fqbn,$(BOARD))
SKETCH = firmware/scale-gateway
FIRMWARE_TEST_BUILD = firmware/tests/build
# Each name is both firmware/tests/test_<name>.cpp and the sketch module it
# covers, so a new host test is one word here.
FIRMWARE_TESTS = scale_protocol delivery_queue
BAUD   = 115200
SCALE_DB_PATH   ?= server/weights.db
SCALE_DATA_DIR  ?= server/data
# The container writes the database into a bind-mounted host directory, so it
# has to run as the user that owns it.
COMPOSE = SCALE_UID=$(shell id -u) SCALE_GID=$(shell id -g) SCALE_DATA_DIR=$(abspath $(SCALE_DATA_DIR)) \
          docker compose -f server/compose.yaml
CORE   = esp32:esp32@3.3.11
# Every esptool command names the chip it connected to, and read-mac is the
# quietest of them. Recursive, so only the targets that need a board go looking
# for one.
ESPTOOL = $(firstword $(wildcard $(shell arduino-cli config get directories.data)/packages/esp32/tools/esptool_py/*/esptool))
read_board = $(shell $(ESPTOOL) --port $(PORT) --no-stub read-mac 2>/dev/null \
               | sed -n 's/^Chip type: *\([A-Za-z0-9-]*\).*/\1/p' | tr 'A-Z' 'a-z' | tr -d '-')
# Reading the chip resets it, so the first look overwrites this with what it
# found and every later reference gets the answer instead of another reset.
ATTACHED_BOARD = $(eval ATTACHED_BOARD := $(read_board))$(ATTACHED_BOARD)
# The first four are the UART bridges. The chip's own USB comes last, because a
# board that has a bridge is flashed and monitored through it whether or not its
# second port is also plugged in.
PORT  ?= $(firstword $(wildcard /dev/serial/by-id/*10c4*) \
                     $(wildcard /dev/serial/by-id/*Silicon_Labs*) \
                     $(wildcard /dev/serial/by-id/*1a86*) \
                     $(wildcard /dev/serial/by-id/*0403*) \
                     $(wildcard /dev/serial/by-id/*Espressif*))

.DEFAULT_GOAL := compile
.PHONY: install compile upload monitor firmware-test server server-test server-up server-down provision ping show
.PHONY: require-port require-board

install:
	arduino-cli core install $(CORE)

compile:
	arduino-cli compile --fqbn $(FQBN) $(SKETCH)

# Neither dev module declares a USB VID/PID, so the port says nothing about
# which chip answers on it — only the chip itself knows.
upload: require-board
	@echo "$(ATTACHED_BOARD) on $(PORT)"
	arduino-cli compile --fqbn $(call board_fqbn,$(ATTACHED_BOARD)) --upload -p $(PORT) $(SKETCH)

# These modules carry no Arduino headers, so they build and run here rather than
# needing a board, someone to stand on the scale and a day of waiting.
firmware-test:
	@mkdir -p $(FIRMWARE_TEST_BUILD)
	@set -e; for name in $(FIRMWARE_TESTS); do \
	    g++ -std=c++17 -Wall -Wextra -Werror -I$(SKETCH) -o $(FIRMWARE_TEST_BUILD)/$$name \
	        firmware/tests/test_$$name.cpp $(SKETCH)/$$name.cpp; \
	    $(FIRMWARE_TEST_BUILD)/$$name; \
	done

# RTS drives EN and DTR drives IO0 through the auto-reset circuit, and the board
# only runs while both stay asserted, which is what the monitor does by default.
monitor: require-port
	arduino-cli monitor -p $(PORT) --config baudrate=$(BAUD)

server:
	SCALE_DB_PATH=$(SCALE_DB_PATH) uv run --project server scale-server

server-test:
	uv run --project server pytest

# Docker would create a missing bind-mount source itself, owned by root, and the
# container could not then write to it.
server-up:
	@mkdir -p $(SCALE_DATA_DIR)
	$(COMPOSE) up -d --build

server-down:
	$(COMPOSE) down

provision: require-port
	@python3 tools/provision.py $(PORT) $(BAUD)

ping: require-port
	@python3 tools/console.py $(PORT) $(BAUD) ping

show: require-port
	@python3 tools/console.py $(PORT) $(BAUD) show

require-port:
	@test -n "$(PORT)" || { echo "No serial port matched — is the board plugged in?"; exit 1; }

require-board: require-port
	@test -n "$(ESPTOOL)" || { echo "No esptool in the installed core — run 'make install'"; exit 1; }
	@test -n "$(ATTACHED_BOARD)" || { echo "Could not read the chip on $(PORT) — is another program holding the port?"; exit 1; }
