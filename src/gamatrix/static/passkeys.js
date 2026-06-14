(function () {
  "use strict";

  function decode(value) {
    const base64 = value.replace(/-/g, "+").replace(/_/g, "/");
    const bytes = atob(base64);
    return Uint8Array.from(bytes, (char) => char.charCodeAt(0));
  }

  function encode(value) {
    const bytes = new Uint8Array(value);
    let binary = "";
    bytes.forEach((byte) => { binary += String.fromCharCode(byte); });
    return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  function creationOptions(options) {
    options.challenge = decode(options.challenge);
    options.user.id = decode(options.user.id);
    (options.excludeCredentials || []).forEach((item) => { item.id = decode(item.id); });
    return options;
  }

  function requestOptions(options) {
    options.challenge = decode(options.challenge);
    (options.allowCredentials || []).forEach((item) => { item.id = decode(item.id); });
    return options;
  }

  function credentialJSON(credential) {
    const response = credential.response;
    const result = {
      id: credential.id,
      rawId: encode(credential.rawId),
      type: credential.type,
      authenticatorAttachment: credential.authenticatorAttachment,
      response: {
        clientDataJSON: encode(response.clientDataJSON),
      },
    };
    if (response.attestationObject) {
      result.response.attestationObject = encode(response.attestationObject);
      result.response.transports = response.getTransports ? response.getTransports() : [];
    } else {
      result.response.authenticatorData = encode(response.authenticatorData);
      result.response.signature = encode(response.signature);
      result.response.userHandle = response.userHandle ? encode(response.userHandle) : null;
    }
    return result;
  }

  async function json(url, options) {
    const response = await fetch(url, options);
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || "Passkey request failed.");
    return body;
  }

  function showError(error) {
    const target = document.getElementById("passkey-error");
    target.textContent = error.message || String(error);
    target.hidden = false;
  }

  function clearError() {
    const target = document.getElementById("passkey-error");
    if (!target) return;
    target.hidden = true;
    target.textContent = "";
  }

  function setWaiting(visible) {
    const waiting = document.getElementById("passkey-waiting");
    if (waiting) waiting.hidden = !visible;
    const submit = document.querySelector("#passkey-register button[type=submit]");
    if (submit) submit.disabled = visible;
  }

  async function login(mediation) {
    const ceremony = await json("/auth/passkeys/authenticate/options", { method: "POST" });
    const credential = await navigator.credentials.get({
      publicKey: requestOptions(ceremony.publicKey),
      mediation: mediation,
    });
    if (!credential) return;
    const result = await json("/auth/passkeys/authenticate/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ challenge_id: ceremony.challenge_id, credential: credentialJSON(credential) }),
    });
    window.location.assign(result.redirect);
  }

  function enableLogin() {
    if (!window.PublicKeyCredential) return;
    document.getElementById("passkey-login").addEventListener("click", () => {
      clearError();
      login("required").catch(showError);
    });
    if (PublicKeyCredential.isConditionalMediationAvailable) {
      PublicKeyCredential.isConditionalMediationAvailable().then((available) => {
        if (available) {
          login("conditional").catch(() => {
            clearError();
          });
        }
      });
    }
  }

  function enableManagement() {
    document.getElementById("passkey-register").addEventListener("submit", async (event) => {
      event.preventDefault();
      clearError();
      try {
        const form = new FormData(event.target);
        const ceremony = await json("/auth/passkeys/register/options", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ friendly_name: form.get("friendly_name"), password: form.get("password") }),
        });
        setWaiting(true);
        const credential = await navigator.credentials.create({ publicKey: creationOptions(ceremony.publicKey) });
        await json("/auth/passkeys/register/verify", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ challenge_id: ceremony.challenge_id, credential: credentialJSON(credential) }),
        });
        setWaiting(false);
        window.location.assign("/auth/passkeys/list");
      } catch (error) {
        setWaiting(false);
        showError(error);
      }
    });
  }

  window.gamatrixPasskeys = { enableLogin, enableManagement };
}());
