mergeInto(LibraryManager.library, {
  NanaHairSecondaryState: function (json) {
    var state = JSON.parse(UTF8ToString(json));
    window.__nanaHairSecondary = state;
    window.dispatchEvent(new CustomEvent('nana-hair-secondary-state', { detail: state }));
  }
});
