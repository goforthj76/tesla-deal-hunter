"""Local-only masked credential setup. Never sends credentials to the browser."""
from pathlib import Path
import re
import tkinter as tk
from tkinter import messagebox

ROOT = Path(__file__).resolve().parent

def save_key(key):
    key=key.strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{16,200}',key):
        raise ValueError('Paste the API key only, without spaces or quotes.')
    path=ROOT/'.env'
    text=path.read_text(encoding='utf-8') if path.exists() else (ROOT/'.env.example').read_text(encoding='utf-8')
    line='MARKETCHECK_API_KEY='+key
    if re.search(r'^MARKETCHECK_API_KEY=',text,re.M):
        text=re.sub(r'^MARKETCHECK_API_KEY=.*$',lambda _:line,text,flags=re.M)
    else: text+='\n'+line+'\n'
    path.write_text(text,encoding='utf-8')

if __name__=='__main__':
    window=tk.Tk();window.title('Tesla Deal Hunter setup');window.geometry('520x220')
    tk.Label(window,text='MarketCheck API key',font=('Segoe UI',15)).pack(pady=15)
    field=tk.Entry(window,show='•',width=58);field.pack(padx=20);field.focus_set()
    tk.Label(window,text='Saved only on this computer. Your current key is never displayed.').pack(pady=10)
    def save():
        try: save_key(field.get())
        except ValueError as e: messagebox.showerror('Check key',str(e));return
        field.delete(0,tk.END)
        messagebox.showinfo('Saved','API key saved. Restart Tesla Deal Hunter to use it.');window.destroy()
    tk.Button(window,text='Save key',command=save,width=20).pack(pady=10)
    window.mainloop()
