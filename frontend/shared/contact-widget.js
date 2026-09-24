// IMKONEX main-site messenger, local trusted icons; never executes remote Tilda code.
(() => {
  'use strict';
  const script = document.currentScript;
  const origin = script?.dataset.origin || location.origin;
  if (window.self !== window.top || new URLSearchParams(location.search).get('embed') === 'tilda' || document.documentElement.classList.contains('imx-shell-embedded')) return;
  if (window.__imkonexContactsMounted) return;
  window.__imkonexContactsMounted = true;
  const markup = "<aside id=\"imxMessenger\" class=\"imx-messenger\" data-site-chrome aria-label=\"Связаться с IMKONEX\">\n<button id=\"imxMessengerToggle\" class=\"imx-messenger__toggle\" type=\"button\" aria-expanded=\"false\" aria-controls=\"imxMessengerLinks\" aria-label=\"Открыть мессенджеры\"><svg aria-hidden=\"true\" role=\"presentation\" class=\"imx-messenger__write\" width=\"35\" height=\"32\" viewbox=\"0 0 35 32\" xmlns=\"http://www.w3.org/2000/svg\"> <path d=\"M11.2667 12.6981H23.3667M11.2667 16.4717H23.3667M4.8104 23.5777C2.4311 21.1909 1 18.1215 1 14.7736C1 7.16679 8.38723 1 17.5 1C26.6128 1 34 7.16679 34 14.7736C34 22.3804 26.6128 28.5472 17.5 28.5472C15.6278 28.5472 13.8286 28.2868 12.1511 27.8072L12 27.7925L5.03333 31V23.8219L4.8104 23.5777Z\" stroke=\"#ffffff\" stroke-width=\"2\" stroke-miterlimit=\"10\" stroke-linecap=\"round\" stroke-linejoin=\"round\" fill=\"none\"></path> </svg><svg aria-hidden=\"true\" role=\"presentation\" xmlns=\"http://www.w3.org/2000/svg\" width=\"16\" height=\"16\" class=\"imx-messenger__close\" viewbox=\"0 0 23 23\"> <g fillrule=\"evenodd\"> <path d=\"M10.314 -3.686H12.314V26.314H10.314z\" transform=\"rotate(-45 11.314 11.314)\"></path> <path d=\"M10.314 -3.686H12.314V26.314H10.314z\" transform=\"rotate(45 11.314 11.314)\"></path> </g> </svg></button>\n<nav id=\"imxMessengerLinks\" class=\"imx-messenger__links\" aria-label=\"Мессенджеры и телефон\" hidden>\n<a class=\"imx-messenger__link\" href=\"https://wa.me/79525205454\" target=\"_blank\" rel=\"nofollow noopener noreferrer\" aria-label=\"Написать в WhatsApp\" data-contact-channel=\"whatsapp\"><span class=\"imx-messenger__label\">Написать в WhatsApp</span><svg aria-hidden=\"true\" role=\"presentation\" width=\"50\" height=\"50\" fill=\"none\" xmlns=\"http://www.w3.org/2000/svg\"><path d=\"M25 50a25 25 0 100-50 25 25 0 000 50z\" fill=\"#fff\"></path><path d=\"M26.1 12a12.1 12.1 0 00-10.25 18.53l.29.46-1.22 4.46 4.57-1.2.45.27a12.1 12.1 0 106.16-22.51V12zm6.79 17.22c-.3.85-1.72 1.62-2.41 1.72-.62.1-1.4.14-2.25-.14-.7-.22-1.37-.47-2.03-.77-3.59-1.57-5.93-5.24-6.1-5.48-.19-.24-1.47-1.97-1.47-3.76 0-1.79.93-2.67 1.25-3.03.33-.37.72-.46.96-.46.23 0 .47 0 .68.02.22 0 .52-.09.8.62l1.1 2.7c.1.18.16.4.04.64s-.18.39-.36.6c-.18.21-.38.47-.54.64-.18.18-.36.38-.15.74.2.36.92 1.55 1.98 2.52 1.37 1.23 2.52 1.62 2.88 1.8.35.18.56.15.77-.1.2-.23.9-1.05 1.13-1.42.24-.36.48-.3.8-.18.33.12 2.09 1 2.44 1.18.36.19.6.28.69.43.09.15.09.88-.21 1.73z\" fill=\"#27D061\"></path><path d=\"M25 0a25 25 0 100 50 25 25 0 000-50zm1.03 38.37c-2.42 0-4.8-.6-6.9-1.76l-7.67 2 2.05-7.45a14.3 14.3 0 01-1.93-7.2c0-7.92 6.49-14.38 14.45-14.38a14.4 14.4 0 110 28.79z\" fill=\"#27D061\"></path></svg></a>\n<a class=\"imx-messenger__link\" href=\"https://iimax.ru/Imkonex\" target=\"_blank\" rel=\"nofollow noopener noreferrer\" aria-label=\"Написать в Max\" data-contact-channel=\"max\"><span class=\"imx-messenger__label\">Написать в Max</span><svg aria-hidden=\"true\" class=\"t-sociallinks__svg\" role=\"presentation\" width=\"50px\" height=\"50px\" viewbox=\"0 0 48 48\" fill=\"none\" xmlns=\"http://www.w3.org/2000/svg\"> <path fill-rule=\"evenodd\" clip-rule=\"evenodd\" d=\"M24 0C37.2548 0 48 10.7452 48 24C48 37.2548 37.2548 48 24 48C10.7452 48 0 37.2548 0 24C0 10.7452 10.7452 0 24 0ZM24.2314 12.5C17.8663 12.5 12.4942 17.4255 12.4941 23.9727C12.4941 26.714 13.0015 28.6059 13.4482 30.3047C13.8233 31.6836 14.1543 32.9467 14.1543 34.4414C14.3143 36.4326 17.9823 35.2685 19.1406 33.7793C20.9718 35.1031 22.0251 35.4346 24.292 35.4346C30.5586 35.4011 35.6151 30.2999 35.5938 24.0332C35.5937 17.6682 30.602 12.5 24.2314 12.5ZM24.3857 18.1592V18.165C27.5981 18.349 30.0709 21.0719 29.9453 24.2871C29.7296 27.4955 26.9854 29.9406 23.7734 29.7861C22.768 29.7055 21.8016 29.3614 20.9717 28.7881C20.4699 29.2899 19.6648 29.9402 19.3447 29.8633C18.6774 29.6868 17.8938 26.2951 18.335 23.5098C18.87 20.1452 21.2859 17.9993 24.3857 18.1592Z\" fill=\"#4db7ff\"></path> </svg></a>\n<a class=\"imx-messenger__link\" href=\"https://t.me/IMKONEX_GROUP\" target=\"_blank\" rel=\"nofollow noopener noreferrer\" aria-label=\"Написать в Telegram\" data-contact-channel=\"telegram\"><span class=\"imx-messenger__label\">Написать в Telegram</span><svg aria-hidden=\"true\" class=\"t-sociallinks__svg\" role=\"presentation\" width=\"50px\" height=\"50px\" viewbox=\"0 0 100 100\" fill=\"none\" xmlns=\"http://www.w3.org/2000/svg\"> <path fill-rule=\"evenodd\" clip-rule=\"evenodd\" d=\"M50 100c27.614 0 50-22.386 50-50S77.614 0 50 0 0 22.386 0 50s22.386 50 50 50Zm21.977-68.056c.386-4.38-4.24-2.576-4.24-2.576-3.415 1.414-6.937 2.85-10.497 4.302-11.04 4.503-22.444 9.155-32.159 13.734-5.268 1.932-2.184 3.864-2.184 3.864l8.351 2.577c3.855 1.16 5.91-.129 5.91-.129l17.988-12.238c6.424-4.38 4.882-.773 3.34.773l-13.49 12.882c-2.056 1.804-1.028 3.35-.129 4.123 2.55 2.249 8.82 6.364 11.557 8.16.712.467 1.185.778 1.292.858.642.515 4.111 2.834 6.424 2.319 2.313-.516 2.57-3.479 2.57-3.479l3.083-20.226c.462-3.511.993-6.886 1.417-9.582.4-2.546.705-4.485.767-5.362Z\" fill=\"#1d98dc\"></path> </svg></a>\n<a class=\"imx-messenger__link\" href=\"tel:+78003013688\" rel=\"nofollow noopener noreferrer\" aria-label=\"Позвонить\" data-contact-channel=\"phone\"><span class=\"imx-messenger__label\">Позвонить</span><svg aria-hidden=\"true\" class=\"t-sociallinks__svg\" role=\"presentation\" width=\"50px\" height=\"50px\" viewbox=\"0 0 100 100\" fill=\"none\" xmlns=\"http://www.w3.org/2000/svg\"> <path fill-rule=\"evenodd\" clip-rule=\"evenodd\" d=\"M50 100C77.6142 100 100 77.6142 100 50C100 22.3858 77.6142 0 50 0C22.3858 0 0 22.3858 0 50C0 77.6142 22.3858 100 50 100ZM50.0089 29H51.618C56.4915 29.0061 61.1633 30.9461 64.6073 34.3938C68.0512 37.8415 69.9856 42.5151 69.9856 47.3879V48.9968C69.9338 49.5699 69.6689 50.1027 69.2433 50.49C68.8177 50.8772 68.2623 51.0908 67.6868 51.0884H67.5029C66.8966 51.0358 66.3359 50.745 65.9437 50.2796C65.5516 49.8143 65.36 49.2124 65.4109 48.6061V47.3879C65.4109 43.7303 63.9578 40.2225 61.3711 37.6362C58.7844 35.0499 55.2761 33.597 51.618 33.597H50.3997C49.79 33.6488 49.1847 33.4563 48.7169 33.0619C48.2492 32.6675 47.9573 32.1035 47.9054 31.4939C47.8536 30.8843 48.0461 30.279 48.4406 29.8114C48.835 29.3437 49.3992 29.0518 50.0089 29ZM56.889 49.0132C56.4579 48.5821 56.2157 47.9975 56.2157 47.3879C56.2157 46.1687 55.7313 44.9994 54.869 44.1373C54.0068 43.2752 52.8374 42.7909 51.618 42.7909C51.0083 42.7909 50.4236 42.5488 49.9925 42.1177C49.5614 41.6867 49.3192 41.102 49.3192 40.4924C49.3192 39.8828 49.5614 39.2982 49.9925 38.8672C50.4236 38.4361 51.0083 38.1939 51.618 38.1939C54.0568 38.1939 56.3956 39.1626 58.1201 40.8868C59.8445 42.611 60.8133 44.9495 60.8133 47.3879C60.8133 47.9975 60.5711 48.5821 60.14 49.0132C59.7089 49.4442 59.1242 49.6864 58.5145 49.6864C57.9048 49.6864 57.3201 49.4442 56.889 49.0132ZM66.4011 69.0663L66.401 69.0846C66.3999 69.5725 66.2967 70.0547 66.0981 70.5003C65.8998 70.9451 65.611 71.3435 65.2499 71.67C64.8674 72.0182 64.4123 72.2771 63.9176 72.428C63.4516 72.5702 62.9613 72.6132 62.4782 72.5546C58.2475 72.53 53.4102 70.5344 49.1802 68.1761C44.8871 65.7827 41.0444 62.915 38.8019 60.9903L38.7681 60.9613L38.7367 60.9299C32.3303 54.5198 28.2175 46.1735 27.0362 37.186C26.9623 36.6765 27.0018 36.157 27.1519 35.6645C27.3027 35.1695 27.5615 34.7142 27.9094 34.3314C28.2397 33.9658 28.6436 33.6742 29.0944 33.4757C29.5447 33.2775 30.0316 33.1766 30.5234 33.1796H37.4967C38.299 33.1636 39.0826 33.4244 39.7156 33.9184C40.3527 34.4156 40.7979 35.1184 40.9754 35.9071L41.0038 36.0335V36.1631C41.0038 36.4901 41.0787 36.795 41.1847 37.2268C41.2275 37.4012 41.2755 37.5965 41.3256 37.8221L41.326 37.8238C41.583 38.9896 41.925 40.1351 42.3491 41.251L42.7322 42.259L38.4899 44.26L38.4846 44.2625C38.204 44.3914 37.986 44.6263 37.8784 44.9157L37.8716 44.934L37.8642 44.952C37.7476 45.236 37.7476 45.5545 37.8642 45.8385L37.9144 45.9608L37.9359 46.0912C38.0802 46.9648 38.5603 48.0981 39.4062 49.4169C40.243 50.7215 41.3964 52.1437 42.808 53.5872C45.6206 56.4634 49.3981 59.3625 53.5798 61.5387C53.8533 61.6395 54.1552 61.6343 54.4257 61.5231L54.4437 61.5157L54.462 61.5089C54.7501 61.4016 54.9842 61.1848 55.1133 60.9057L55.1148 60.9023L57.0232 56.6591L58.0397 57.03C59.1934 57.4509 60.3737 57.7947 61.5729 58.0592L61.5785 58.0605L61.5841 58.0618C62.152 58.1929 62.7727 58.3042 63.3802 58.3942L63.4231 58.4006L63.4654 58.4101C64.2537 58.5877 64.956 59.0332 65.453 59.6706C65.9429 60.2991 66.2033 61.0758 66.1916 61.8721L66.4011 69.0663Z\" fill=\"#000\"></path> </svg></a>\n</nav>\n</aside>";
  function start() {
    if (document.getElementById('imxMessenger') || document.querySelector('.t898')) return;
    const sheet = document.createElement('link'); sheet.rel='stylesheet'; sheet.href=origin+'/shared/contact-widget.css'; document.head.append(sheet);
    const template=document.createElement('template'); template.innerHTML=markup;
    const widget=template.content.firstElementChild; widget.hidden=true;
    const toggle=widget.querySelector('#imxMessengerToggle'), links=widget.querySelector('#imxMessengerLinks');
    document.body.append(widget);
    // Prevent a flash of unstyled icons; a CSS failure leaves the existing header contacts usable.
    let styled=false;
    sheet.addEventListener('load',()=>{styled=true;refreshOverlay();});
    function setOpen(open,restore=false) {
      links.hidden=!open;toggle.setAttribute('aria-expanded',String(open));
      toggle.setAttribute('aria-label',open?'Закрыть мессенджеры':'Открыть мессенджеры');
      if(restore)toggle.focus();
    }
    function safe(channel,value) {
      if(typeof value!=='string'||value.length>400)return null;
      try {
        const u=new URL(value);
        if(u.username||u.password)return null;
        if(channel==='phone')return /^tel:\+?\d{7,15}$/.test(value)?value:null;
        const hosts={whatsapp:['wa.me'],max:['iimax.ru','max.ru'],telegram:['t.me']};
        return u.protocol==='https:'&&hosts[channel]?.includes(u.hostname)&&!u.port?u.href:null;
      }catch{return null;}
    }
    function configure(items) {
      if(!Array.isArray(items))return;
      const clean=new Map(items.map(item=>[item?.channel,safe(item?.channel,item?.url)]).filter(([,url])=>url));
      if(!clean.size)return;
      for(const a of links.querySelectorAll('[data-contact-channel]')){
        const value=clean.get(a.dataset.contactChannel);a.hidden=!value;if(value)a.href=value;
      }
    }
    function refreshOverlay() {
      if(!document?.body||!widget.isConnected)return;
      const shell=document.querySelector('imkonex-header')?.shadowRoot;
      const busy=document.body.classList.contains('filters-open')||document.body.classList.contains('lightbox-open')||
        !!document.querySelector('dialog[open], [aria-modal="true"]:not([hidden]), #filters.open, [data-contact-obscured="true"]')||
        !!shell?.querySelector('.is-menu-open');
      widget.hidden=!styled||busy;
      if(busy)setOpen(false);
    }
    toggle.addEventListener('click',()=>setOpen(links.hidden));
    document.addEventListener('click',e=>{if(!widget.contains(e.target))setOpen(false);});
    document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!links.hidden){e.preventDefault();setOpen(false,true);}});
    document.addEventListener('imkonex:contacts',e=>configure(e.detail));
    document.addEventListener('imkonex:shell-menu',refreshOverlay);
    const observer=new MutationObserver(refreshOverlay);
    observer.observe(document.body,{subtree:true,childList:true,attributes:true,attributeFilter:['open','class','aria-modal','data-contact-obscured']});
    window.addEventListener('pagehide',()=>observer.disconnect(),{once:true});
    configure(window.IMKONEX_SITE_CONTACTS);refreshOverlay();
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});else start();
})();
