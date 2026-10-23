#include "console.h"

#include <Arduino.h>

#include "config.h"
#include "gateway_settings.h"
#include "publisher.h"
#include "wifi_link.h"

namespace {

const size_t MAX_COMMAND_LENGTH = 160;

struct Command {
  const char *name;
  // What the command takes after its name, or nullptr when it takes nothing.
  // This is what decides whether an empty rest of the line is an error, so the
  // help text and the check are the same fact rather than two that must agree.
  const char *argument;
  const char *help;
  void (*run)(const String &value);
};

void showHelp(const String &value);

void storeNetwork(const String &value) {
  gatewaySettings::setNetwork(value);
  Serial.println("stored the network");
}

void storePassphrase(const String &value) {
  gatewaySettings::setPassphrase(value);
  Serial.println("stored the passphrase");
}

void storeServerUrl(const String &value) {
  gatewaySettings::setServerUrl(value);
  owePing();
  Serial.println("stored the url");
}

void storeSigningKey(const String &value) {
  gatewaySettings::setSigningKey(value);
  Serial.println("stored the signing key");
}

// The watched scales on one line, the way they were typed in.
String scaleList() {
  String joined;
  for (size_t i = 0; i < gatewaySettings::scaleCount(); i++) {
    if (i > 0) {
      joined += ' ';
    }
    joined += gatewaySettings::scaleAddress(i);
  }
  return joined;
}

void storeScaleAddresses(const String &value) {
  if (!gatewaySettings::setScaleAddresses(value)) {
    Serial.printf(
      "not a list of addresses — expected up to %d like 08:B8:D0:12:34:56, separated by spaces\n", MAX_SCALES
    );
    return;
  }
  Serial.printf("listening for %s\n", scaleList().c_str());
}

void pingNow(const String &value) {
  (void)value;
  pingServer();
}

void showSettings(const String &value) {
  (void)value;
  String network = gatewaySettings::network();
  Serial.printf("network:    %s\n", network.isEmpty() ? "(unset)" : network.c_str());
  Serial.printf("passphrase: %s\n", gatewaySettings::hasPassphrase() ? "(set)" : "(unset)");
  String url = gatewaySettings::serverUrl();
  Serial.printf("url:        %s\n", url.isEmpty() ? "(unset)" : url.c_str());
  Serial.printf("key:        %s\n", gatewaySettings::hasSigningKey() ? "(set)" : "(unset)");
  String scales = scaleList();
  Serial.printf("scales:     %s\n", scales.isEmpty() ? "(unset)" : scales.c_str());
  Serial.printf("this board: %s\n", hardwareAddress().c_str());
  if (isWiFiConnected()) {
    Serial.printf("wifi:       connected as %s\n", wifiAddress().c_str());
  } else {
    Serial.printf("wifi:       not connected — %s\n", describeDisconnect().c_str());
  }
  Serial.printf("held:       %u weigh-in(s) waiting for the server\n", static_cast<unsigned>(heldWeighIns()));
  // Verifying the server's certificate chain is the largest allocation this
  // board makes, and it needs the memory in one piece, so both numbers answer
  // whether the next https POST has anywhere to happen.
  Serial.printf(
    "heap:       %u free, largest block %u\n", static_cast<unsigned>(ESP.getFreeHeap()),
    static_cast<unsigned>(ESP.getMaxAllocHeap())
  );
}

void connectNow(const String &value) {
  (void)value;
  connectWiFi();
}

void forgetEverything(const String &value) {
  (void)value;
  gatewaySettings::forget();
  disconnectWiFi();
  Serial.println("erased every stored setting");
}

const Command COMMANDS[] = {
  {"network",    "<name>",       "the WiFi network to join",                 storeNetwork},
  {"passphrase", "<text>",       "its passphrase, never printed back",       storePassphrase},
  {"url",        "<base>",       "where the server is, without a route",     storeServerUrl},
  {"key",        "<secret>",     "the key deliveries are signed with",       storeSigningKey},
  {"scale",      "<address>...", "the scales to listen for, as MACs",        storeScaleAddresses},
  {"ping",       nullptr,        "report to the server now",                 pingNow},
  {"show",       nullptr,        "the stored settings, secrets withheld",    showSettings},
  {"connect",    nullptr,        "apply the stored settings now",            connectNow},
  {"forget",     nullptr,        "erase every stored setting",               forgetEverything},
  {"help",       nullptr,        "this list",                                showHelp},
};

void showHelp(const String &value) {
  (void)value;
  Serial.println("commands:");
  for (const Command &command : COMMANDS) {
    char usage[32];
    snprintf(usage, sizeof(usage), "%s %s", command.name, command.argument == nullptr ? "" : command.argument);
    Serial.printf("  %-20s %s\n", usage, command.help);
  }
  Serial.println("\nEverything after the command word is taken verbatim, so spaces are fine.");
}

void runCommand(const String &line) {
  int separator = line.indexOf(' ');
  String name = separator < 0 ? line : line.substring(0, separator);
  String value = separator < 0 ? String() : line.substring(separator + 1);
  value.trim();

  for (const Command &command : COMMANDS) {
    if (name != command.name) {
      continue;
    }
    if (command.argument != nullptr && value.isEmpty()) {
      Serial.printf("%s needs a value\n", command.name);
      return;
    }
    command.run(value);
    return;
  }

  Serial.printf("unknown command: %s — type 'help'\n", name.c_str());
}

}  // namespace

// Nothing typed here is echoed: a secret that the board never sends back cannot
// be read off the console by someone glancing at the screen.
void readCommands() {
  static String pending;
  while (Serial.available()) {
    char character = Serial.read();
    if (character == '\n' || character == '\r') {
      if (!pending.isEmpty()) {
        runCommand(pending);
        pending = "";
      }
      continue;
    }
    if (pending.length() < MAX_COMMAND_LENGTH) {
      pending += character;
    }
  }
}
