#include "request_signature.h"

#include <mbedtls/md.h>
#include <stdint.h>
#include <string.h>

const char *const SIGNATURE_HEADER = "X-Signature";

namespace {

// The digest is named in the value it produces, so a second algorithm could be
// accepted alongside this one instead of having to replace it on both sides at
// the same moment.
const char *const DIGEST_LABEL = "sha256=";

const char HEX_DIGITS[] = "0123456789abcdef";

// The parts of a request are fed to the digest separated by a byte none of them
// can contain, so no request can be dressed up as another by moving a boundary
// between them.
const char *const PART_SEPARATOR = "\n";

bool feed(mbedtls_md_context_t *digest, const char *part) {
  return mbedtls_md_hmac_update(digest, reinterpret_cast<const uint8_t *>(part), strlen(part)) == 0;
}

}  // namespace

bool signRequest(const char *key, const char *method, const char *path, const char *body, char *out) {
  const mbedtls_md_info_t *sha256 = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
  if (sha256 == nullptr) {
    return false;
  }

  mbedtls_md_context_t digest;
  mbedtls_md_init(&digest);

  uint8_t hash[32];
  const bool computed = mbedtls_md_setup(&digest, sha256, 1) == 0 &&
                       mbedtls_md_hmac_starts(&digest, reinterpret_cast<const uint8_t *>(key), strlen(key)) == 0 &&
                       feed(&digest, method) && feed(&digest, PART_SEPARATOR) && feed(&digest, path) &&
                       feed(&digest, PART_SEPARATOR) && feed(&digest, body) &&
                       mbedtls_md_hmac_finish(&digest, hash) == 0;

  mbedtls_md_free(&digest);
  if (!computed) {
    return false;
  }

  strcpy(out, DIGEST_LABEL);
  char *hex = out + strlen(DIGEST_LABEL);
  for (size_t i = 0; i < sizeof(hash); i++) {
    *hex++ = HEX_DIGITS[hash[i] >> 4];
    *hex++ = HEX_DIGITS[hash[i] & 0x0F];
  }
  *hex = '\0';
  return true;
}
