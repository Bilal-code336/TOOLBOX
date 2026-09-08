// Lógica oficial del traductor de Google para TOOLBOX
function googleTranslateElementInit() {
  new google.translate.TranslateElement({
    pageLanguage: 'es', 
    includedLanguages: 'en,es,fr,de,it', 
    layout: google.translate.TranslateElement.InlineLayout.SIMPLE
  }, 'google_translate_element');
}

// Carga dinámicamente el script externo de Google sin ensuciar el HTML
const scriptGoogle = document.createElement('script');
scriptGoogle.type = 'text/javascript';
scriptGoogle.src = 'https://google.com';
document.body.appendChild(scriptGoogle);
