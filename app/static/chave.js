/* Chaves de acesso (WebAuthn): reconhecimento facial, digital ou Windows Hello. */
(function () {
  function b64uParaBuf(s) {
    s = s.replace(/-/g, '+').replace(/_/g, '/'); while (s.length % 4) s += '=';
    var bin = atob(s), u = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i);
    return u.buffer;
  }
  function bufParaB64u(b) {
    var u = new Uint8Array(b), s = '';
    for (var i = 0; i < u.length; i++) s += String.fromCharCode(u[i]);
    return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }
  function suportado() { return !!(window.PublicKeyCredential && navigator.credentials && window.isSecureContext); }

  function criar(o) {
    var pk = Object.assign({}, o, {
      challenge: b64uParaBuf(o.challenge),
      user: Object.assign({}, o.user, { id: b64uParaBuf(o.user.id) }),
      excludeCredentials: (o.excludeCredentials || []).map(function (c) { return Object.assign({}, c, { id: b64uParaBuf(c.id) }); })
    });
    return navigator.credentials.create({ publicKey: pk }).then(function (c) {
      return { id: c.id, rawId: bufParaB64u(c.rawId), type: c.type,
        response: { clientDataJSON: bufParaB64u(c.response.clientDataJSON), attestationObject: bufParaB64u(c.response.attestationObject),
                    transports: c.response.getTransports ? c.response.getTransports() : [] },
        clientExtensionResults: c.getClientExtensionResults ? c.getClientExtensionResults() : {},
        authenticatorAttachment: c.authenticatorAttachment || null };
    });
  }

  function obter(o) {
    var pk = Object.assign({}, o, {
      challenge: b64uParaBuf(o.challenge),
      allowCredentials: (o.allowCredentials || []).map(function (c) { return Object.assign({}, c, { id: b64uParaBuf(c.id) }); })
    });
    return navigator.credentials.get({ publicKey: pk }).then(function (c) {
      return { id: c.id, rawId: bufParaB64u(c.rawId), type: c.type,
        response: { clientDataJSON: bufParaB64u(c.response.clientDataJSON), authenticatorData: bufParaB64u(c.response.authenticatorData),
                    signature: bufParaB64u(c.response.signature),
                    userHandle: c.response.userHandle ? bufParaB64u(c.response.userHandle) : null },
        clientExtensionResults: c.getClientExtensionResults ? c.getClientExtensionResults() : {},
        authenticatorAttachment: c.authenticatorAttachment || null };
    });
  }

  function postar(url, dados) {
    var fd = new FormData();
    Object.keys(dados).forEach(function (k) { fd.append(k, dados[k]); });
    return fetch(url, { method: 'POST', body: fd, credentials: 'same-origin' }).then(function (r) {
      return r.json().then(function (j) { if (!r.ok && !j.erro) j.erro = 'Erro ' + r.status; return j; });
    });
  }

  function mensagemErro(e) {
    if (e && e.name === 'NotAllowedError') return 'Reconhecimento cancelado ou não confirmado.';
    if (e && e.name === 'InvalidStateError') return 'Este aparelho já está cadastrado.';
    if (e && e.name === 'SecurityError') return 'O reconhecimento só funciona pelo endereço oficial do sistema (https).';
    return (e && e.message) || 'Não foi possível usar o reconhecimento.';
  }

  window.Chave = { suportado: suportado, criar: criar, obter: obter, postar: postar, mensagemErro: mensagemErro };
})();
