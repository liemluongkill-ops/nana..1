mergeInto(LibraryManager.library, {
  NanaAvatarEventState: function (json) {
    var state = JSON.parse(UTF8ToString(json));
    window.__nanaAvatarState = state;
    window.dispatchEvent(new CustomEvent('nana-avatar-state', { detail: state }));
  },
  NanaAvatarModelState: function (json) {
    var state = JSON.parse(UTF8ToString(json));
    window.__nanaAvatarModel = state;
    window.dispatchEvent(new CustomEvent('nana-avatar-model-state', { detail: state }));
  },
  NanaAvatarMouthState: function (json) {
    var state = JSON.parse(UTF8ToString(json));
    window.__nanaAvatarMouth = state;
    window.dispatchEvent(new CustomEvent('nana-avatar-mouth-state', { detail: state }));
  }
});
