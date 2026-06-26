(function (global) {
  "use strict";

  function b64urlToBytes(value) {
    var base64 = String(value || "").replace(/-/g, "+").replace(/_/g, "/");
    while (base64.length % 4) base64 += "=";
    var binary = global.atob(base64);
    var bytes = new Uint8Array(binary.length);
    for (var index = 0; index < binary.length; index += 1) {
      bytes[index] = binary.charCodeAt(index);
    }
    return bytes;
  }

  function bytesToB64url(bytes) {
    var binary = "";
    for (var index = 0; index < bytes.length; index += 1) {
      binary += String.fromCharCode(bytes[index]);
    }
    return global.btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
  }

  function utf8Bytes(value) {
    if (global.TextEncoder) {
      return new TextEncoder().encode(value);
    }
    var encoded = unescape(encodeURIComponent(value));
    var bytes = new Uint8Array(encoded.length);
    for (var index = 0; index < encoded.length; index += 1) {
      bytes[index] = encoded.charCodeAt(index);
    }
    return bytes;
  }

  function bytesToBigInt(bytes) {
    var hex = "";
    for (var index = 0; index < bytes.length; index += 1) {
      var part = bytes[index].toString(16);
      hex += part.length === 1 ? "0" + part : part;
    }
    return BigInt("0x" + (hex || "0"));
  }

  function leftPad(value, size, char) {
    var text = String(value);
    while (text.length < size) text = char + text;
    return text;
  }

  function bigIntToFixedBytes(value, size) {
    var hex = value.toString(16);
    if (hex.length % 2) hex = "0" + hex;
    var expectedLength = size * 2;
    if (hex.length > expectedLength) {
      throw new Error("RSA result is larger than key size");
    }
    hex = leftPad(hex, expectedLength, "0");
    var bytes = new Uint8Array(size);
    for (var index = 0; index < size; index += 1) {
      bytes[index] = parseInt(hex.slice(index * 2, index * 2 + 2), 16);
    }
    return bytes;
  }

  function modPow(base, exponent, modulus) {
    var zero = BigInt(0);
    var one = BigInt(1);
    var result = one;
    var power = base % modulus;
    var exp = exponent;
    while (exp > zero) {
      if ((exp & one) === one) result = (result * power) % modulus;
      exp >>= one;
      power = (power * power) % modulus;
    }
    return result;
  }

  function fillNonZeroRandom(bytes) {
    var cryptoObj = global.crypto || global.msCrypto;
    if (!cryptoObj || !cryptoObj.getRandomValues) {
      throw new Error("Secure random is unavailable");
    }
    cryptoObj.getRandomValues(bytes);
    for (var index = 0; index < bytes.length; index += 1) {
      while (bytes[index] === 0) {
        var single = new Uint8Array(1);
        cryptoObj.getRandomValues(single);
        bytes[index] = single[0];
      }
    }
  }

  function encryptPkcs1v15(jwk, plainBytes) {
    if (typeof BigInt !== "function") {
      throw new Error("BigInt is unavailable");
    }
    var modulusBytes = b64urlToBytes(jwk.n);
    var exponentBytes = b64urlToBytes(jwk.e);
    var keySize = modulusBytes.length;
    if (plainBytes.length > keySize - 11) {
      throw new Error("Login payload is too long");
    }

    var paddingLength = keySize - plainBytes.length - 3;
    var encoded = new Uint8Array(keySize);
    encoded[0] = 0;
    encoded[1] = 2;
    fillNonZeroRandom(encoded.subarray(2, 2 + paddingLength));
    encoded[2 + paddingLength] = 0;
    encoded.set(plainBytes, 3 + paddingLength);

    var encrypted = modPow(bytesToBigInt(encoded), bytesToBigInt(exponentBytes), bytesToBigInt(modulusBytes));
    return bytesToB64url(bigIntToFixedBytes(encrypted, keySize));
  }

  global.LoginRsaFallback = {
    encryptPayload: function (payload, jwk) {
      return "pkcs1v15:" + encryptPkcs1v15(jwk, utf8Bytes(payload));
    }
  };
})(window);
