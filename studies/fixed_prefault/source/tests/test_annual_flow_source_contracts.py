"""Pure registry/AST gates only; no native imports, model, query or optimizer."""
import ast
import base64
from copy import deepcopy
import hashlib
import importlib
from pathlib import Path
import zlib

import pytest
from reviewed_source_fixtures import reviewed_source_bytes

# Complete byte-pinned frozen09 source makes these fixtures independent of
# subsequent DEV leaf edits. It is parsed only, never imported or executed.
_OLD_PHYSICAL_ZLIB_B64=(
    b'eNrNPWtv40aS3wPkP/AyOFC0KVqSJ9k9KQxukniTwWYSY+zs3EEQCIpsSYwpUuHDj/X6v19V9YPNJil7JrnDBcjYJrurq7ur693F'
    b'L7744h+sKJM8Y7FVsWKfZGFqhVGU11mVZNuFlWfpg1XtmMXuD2kSJZX1bvql9f0P1iEvKisprdCKWJJCW+/zzz7/7FvoGAOsTV7c'
    b'WPnG2uXlIanCNEhY6RWshJYsi5gXlHl6y4J9kh48602aWnmRbGnwdZiG0KJ0P/8sCg8hjAhdXetQJNCkerDSPLqBv6M8u2UFoGyt'
    b'cUjAI4uhb3TD4GfBohzePsAv+zDJALNrmEEUpim039dlZd2GaRKHFbMumRVuoQ082yRZAk9gAEBjz6JdmCXw61mRV3lhxQ9ZuE+i'
    b'EoB98cUXn3+2KfK9FQSbuqoLFgRWsqcVCbMsr8IKVrQUbWCYMErDsmSlalTGSVR9/pn4cx9WO/VHVu8PD9DCyg4CQBklhwcvP1TJ'
    b'PvknkzBopUv3pyRjYfEdDFcVMI1K9GmtO611EcjVkaPC2ve13ucxS2WjS9iMyt1WQcpuWRocivwAq447kqRpjWNWCexjFRbJZuO+'
    b'vw6ufwn+/sH9+4cfg8uL98G7d99e/9o3SEMKcqQf3v/y6+WV+92b9+/fXry/clN2H+BeB1UOu4YkgfQVs43FSeeweyhxe0ZFfuce'
    b'EM05RzbNw7icZwcvi8OiCB/c8DZM0nAN41UPc1z4JeDtbqBdtXJL2Cw2f4/kvmdX+AcQnmX8F1fBbk4d3G2RxAFHOUhhQ6rg5oN4'
    b'FcPEYI0AIrwTg6Zsvs7ztAcmrGkR7g/QPYAlVfDFXiE2An7JIv7O/8qb9ACCk7BJtjSxf/0MB9nHf1wLtia6gWOJe7NlVWk26AKC'
    b'gWo8YgGgdmBwFuHXX6++J/RuPggE/UkvEuweOiPVB2GEP8zRmveALqJWGS2c8Tf4YM5Bwwm72sNxtX64HtdwKoHuY+vd258urbuk'
    b'2lnn8Bw401m+2Vhr4BoFrLx1iisBTKvO69I6wB4gIRBTQoi3M2AGFR70htVs8roYH3I4NRYcYgDELJgrsK49kptr3e2SlFnXP769'
    b'sjZhnVYckjyIhVXjkQ6tTZ2mYxzM2rGwGsORYGdlxUI4uSkcFys8wLG5T/bEFZAzWuWBMUQNwQHpZbBNOKN9+GDh+UK+9Hry7+Pp'
    b'ZPLvFv4Vu3zeSDBjogpYjryuDnVlIZvaslJA+4ndJ1G+LcLDLomsy8nZ5fTscnZ2eQ67W9wmeNxKNQNgmAzQYWJQxCLKy8pTe8B/'
    b'QdYFBzRl2YjOmcdSFlVFEgURLBAczjIoKsf3z4kH8yZAQiRD4CkHsp+d+yNOqrAB1uOT4wFVjuyA2A00LoBpVxkry+B2dm47vFey'
    b'wY6IM3BVC+mExoBes/NgncP2FEGcI4+3qUWSWe1B5g2pEhOq4DAKFmeO6XFAiklzNqMItwEEdFQXmfl+hOSLzMhXDMk/NAzJp39b'
    b'vMjX/+CMyKd/XeQ3Pv5jnLQ+5uP3PRzgRX7/Y3MUgzP55oN+JuX3PnX5dvj8hzGSyaN884H7DE/yn3lvjGdyKd980MOn/O4jx22L'
    b'IEGsr6zlD9dBYt18sCaeN3Mt+guO/Lnnfela4brMiwPxgPfX1leudfEd/vIXz/sPVwKwOE1b6fqws6YTl3bcmk5di++cNQWwdQaM'
    b'J7Y4i5meN523RV4f4PWeVRZzd25kTV8DIl+ueIvtwQepGBbIL0YT99xZbHPtybn7lbNIQv+rRcK0x39xpxN5Gtf+dLKAzZxOF0kM'
    b'P2aL5M6fni+S2p++lmj8QnME9nr72irDDasexsD/foOTBkgL6UCLkdbA5+SOW6wo4MjeAi9HkpT87BUph8ioGw3w4ucrK18jQFA9'
    b'SqHjwbQ5J4wX0JJtWIHKRUn6qwSVZJu0Jp0DFdoNyQDQZcbrgoWEQ5yUh7CKdmJwSZWPT4IRRhE7wCz8RxvIbWrPJy7+MrPnU/rl'
    b'3J7P4JfmPNpzIHmbbx38HsMfDRUAOeWoNwOdwrtQjIHi4YY9uKCg1oz4mXkmJPsEIbAvRzqHA24JXSUjlNhie3yEWqaXlFzLHZEw'
    b'H9EojoNN9AdfT+btgwOqZcmsf+DLC9yn0cZ+m5EOjbvMN1Usl/UIODz5jwTpSTJybTmX8H7ljyR69Kerj342knqkmJHvDyybxVJA'
    b'a+o5YhTQlIOYAR+IYZdh1X3UvUZ9YgdbConY6mG7fwsBpgQY1vfAncPiIUgy4O8AEWUQfwfzL0p/9tUpCkYxt0ZsmahoC9oF2pw2'
    b'AurSv6fTmYN68i48sNFrOK4NBP4ezp+YNtgn4Rb4EZBc2S9lab56M7kx8mF3fjgJvYcuhbXZyDZgHUH3kV0fygDOERxGWBHcJJce'
    b'6X9HeRqbbehZ80CbLKiB+R0cuhIm0hrM+dcj73aoG+EEfZ9aJ4JOQ4kGHip1o9aUXJTZRP06cHrljMW4z50E+9fsJsvvMmk+Cihk'
    b'H+vTAFTC7GE0eBJbpxBOoEes4BZPcgsxjw4JHHznWczkEX0GM5MESJH5Z3Jor7bbJdDXjtMlytedKe+TTEyyPRGkzNidwOl1h15H'
    b'9Nr5ZsrG/9ExOnB9YhByyCRbqC7nMzTs9Cez+eoFC3YlVgj1Q+4nYPdRWsMfZbIHAyDMGBoXnEzPFAXbSgG4kE4SULHHgNe72QxA'
    b'VCxDF0vpWT8miBbqDOhGGY/fzabkliiFcYKCKSkkNGWopOEDaPtuI/XgVQxqAunbQgmx2O819zsI6dXVd9O8RF/F7WzmqV5C2QUt'
    b'CgRmRSoOIOyuQYjB4qHVERfhHVjt+CsgAUxtjRrmjg8iYPpGf8GDuuvyj9mXmrXRCOoFagQAANUlRq6bXbNUoNZUbHBWXItHyXA7'
    b'+xIZJrB15VWQfp5AKKcCP8H7YIdh0VkciF7+QPP2dJAxmj07JspIrAy+oIfIZNoHTX+FfUWPpX0QQAO+C/ZKp9wu1eKaih5IR7/X'
    b'CQwrNsQ8++QlI6hIVKyQpCtsKVCJxc7jNvvSjxYgm/XJahCNeHMYlSwJeNLIDWLJcgYEpiVTtHXpkSYNHsROFm0MhFxcaJjwR7NF'
    b'FyP+5nzRy5d6tqK1fcN7YTARfbk4ylI4t8c7RjDz7pL1gUriez8N9+s4tBL3Zp7Up8nJ+ekNf3kgPwYYTOLnhv/0e1x33IoXPBeM'
    b'VQ+OV5znYLmxPR1NZBLfSdZO/gefNxaDLMfTlbMg+0PIXPG+bN6fUT9nsalxbL3RptNIadvJZuP3+BUJSTQrYQ33B25bi05RgB4L'
    b'VKD+yYq85OKp9W65Pax8DYsT4aAIS1gSVP/qMqZX+/26qk/Q6m51T9Yrbsd7fMLA+qh5uj7jjwXlsc3mrOX2/OiBtiufz7fnXQwv'
    b'J975hF4BQw3LEpQeMlmtn3+5tkJrz0Kk2FhaihxUG8rdygdJ+lfJla8ihr6zHCSVVFVByMDmsOKA8gjsMOUUtNCH9TAXLFtZShIU'
    b'uadQXyrQkYW+DuTvb/52ffEeFTjrcjK+PCfLTZhx6LHbhXAO1oyh7nSvHGKvrLcUWODTqorkgGOvhZ8EDk2Bsw2zegN2B8y4GEdI'
    b'15sE5r4HcUz+/pY/844l210lafS4x6Dh8V1NzQDoSMY9sb72zdHw0XTiTY5y7mdw4SrImszA5QTs8JXdIe1T3xhYI55X1rW0rgHC'
    b'zYcxygYQcSgX0KtaugB8B5sPO1FITYJHUrSdevA0y9S1AoTFsnpPfkpl+MwFSmAP3azEUvdZItxWDJRR25022CnsYIPiN5s4Tms2'
    b'lwGzTrl/A37zrXUI67mukxSJ7SwCq5DUBfK0oTuYeyeVvZVoXoUrcp4Qpwfe2+hO6NNt9K7bGbl0rZjtSXBiyAXsNgHlDfu9YTyj'
    b'c64US7a55i8pAjFaEkrLuTtZeXBuR44rH0zFg7NRP48RTATUUHJuEJtpes9E75WjMAI6QYY3XfDfk3DljzkMULAC5bkV1lJRyXZM'
    b'tWNRTzMdPjIqBR8Z07QhkASpQ/iTnDm2mbrJytdlxYnu9lxu7B/fX/0QPCan0yd71QwD3dZymKk5DQ4uXetzgEbA3sbTBsSMuklf'
    b'wkI8Ys2jjp3bow1o57eO3TpyIzA5In9kdFmimbGiJcDfdKuN26oNGLRW/SGDp9egJSOoAcBXvo5heTyxDXWEU5d/RfBK/IbPEVq7'
    b'94yaiPYz3mrqvVBFExu/Xo19pSUJGahQpg2yVyf9RG3CUkofoGGCJMmAi7Bqer2y3pCeC/oA+UFJGURsGUgh+A0DMg+gch3A6OD+'
    b'LxBa6DP1ULREFKLVoZE7PwSF+A56gnFRwdEHnQj1ZRCGObAJkGFbtgYGHlk8PLoBEgY0PXMqjWaqzwXYO1dL2/Nozb7RXHtWQb7s'
    b'699SkaHvSHVWbwx6Wp12m2zCTG/hmARjjjL1eg/9a6dLKfH9KHEnzkpXicWJpVdTfPVRDNBEjsOZNUO88ZerBagj+GMHUhF/RuEh'
    b'AEWyhN95I4wlw0MKIAOtsMIlBcYfA99Oso2LB5dHL1FY+jaexDKvi4j5trKfIxV5t1tT98IDalQI3EFM5N80hENIyUc0tjYniah8'
    b'T/4YQoZYC3qJ1PjYMHhED+QbZwwclKOK/0hU+Q8XhG3coG07jZ4z6gY4WhJbxIyQ7m3H920Gkn8LNvtDsK5Le9511HXhLSUQ0SRo'
    b'lECdngtTj18UXPOdaueMCM7dtrUQnjxgOnhgctROevIQmqJFr7Xgo8JFScoFK7RYHR3HhOxwWhGz9OQscX2cIdmorRvKQ9+Qij9c'
    b't2WiiK1kKUapcX+tPag7+3qP/A8Vdc2rQhFtVFiIsiyRRvPgPbPSKG6L5fkp/EJB7JhL6gVfngmfY4OZJ+HqvsQBwGMFeeK9PnkJ'
    b'cDG7gM/OdKRi128mNEczhCe9KgYtcPbRCWVq+uVza9NuhTIImGiJSU5yIXCXKebEI/MxeoVlhL2IMU/hgUL4PFmqNSYLAUGYaIA6'
    b'ra97TGGtMJoOdtctoOG42uI5xhRpKdugTvm83X14P5p4xssxf+l0Fl9iS2sl174i5thradOrpa5tnQ4qa0d1AgGIdQAdUUqpD8wn'
    b'u/HxCOvniBjYxr7+5QMY5Y+/4XFyp55Dp/E3XWg5Z6/FjuAiEkTuZvBktImPUrDfhK/R1GP5IKDyNOgIpkAPPNVT5whgThFjhx8H'
    b'tMHvXRBKZQJWvOP0mlky6KezDy6c3l/beDI4GN9PQh4bQ6F1/HTyHvzsc+MNDisnJYGLJA9ps3mP+ODJO+Rlgl6aHqFjg90Db2Ij'
    b'NNj4e5/jGRKrcQ9a42fwytg2/KN4pagomGih0KanqGDKSAjpCs5HmhLSTnBpmrq1IPqp6C6NuRSrMWQ1EKi2kfCsGi9NES07hIPv'
    b'HCD7za//FXz344fg8td3l3SADMbDcQST4sRvQ1yIN1HnTXspcCZuhHuDenxmjUZga00cdwTG1qwbt+mjF4DQFuoSNsnp0QS0TCH/'
    b't3Ikp9uBExhfB2Fkz2RzYWsrbim2wbucBJfT4FG0egI+lWwTvnwvkf5ig1e+xtiPqwG8B0nTqZKGfV1AQhITmPC+YqRQ+jO1mDqw'
    b'5TY3e/PtlRxSdAOerPwZR9LAThqnh4HXxXcSr+56SHtDjKVcrsL8kSLksDs7PzGZPID+9pe3PyGHHwLfhg4KZV/OlLECP7x/+72x'
    b'BKiKomgW01d6sFBuOyC+f3txdfGTLZJMPdGMZdAPRCulrfBEht0ZqiGkVvZnaIl99CYvtNGF+1KZeSVPSQYVqYEJC2UGnpS1GcYP'
    b'9mre7MbEM8lPs9n5cXlurFZUpT2QYwLXrehPgN4yt78BvjiAvG5yv3yYIys2MMkhZHo1zTU5Zgp9gcdqI0ybBVQ6rmTsZzNPjUcR'
    b'QFS8+BxYhtjHhg5t5FXOTfX2W56JxrPMyBHN41bodMc0feF25Q5S9MkrbuKZoDD5Psl2rKC8VZ6+NuaAKUjLj0JpNeSOJxj9gq4J'
    b'al1LBw76xUNUl8Dm4Zm1SXbLsopfPpCJdMIq6iB1dOmPexsHRIzyf0gpQ24N7xP9GgPCSPmLP9JhLInkXBLJLiwD2AZQZMT0tIUU'
    b'epOdrs92toETz5E1coyWffm4q44tLvr6dr7Z2Bpz6TCUibFZIFG13kWd2fNuBjo0MQF9DZB6Wqo0gtaNBGVHytSBd+joay5WvM02'
    b'LCyTlu7SzSYZ7DSy38GBA9zFRKwUlNZShenFWT0TZPs7HLFkk0ShbjHo/6F+qi/ZtLNkJZsP5yohLmKoZt5i417AoP6w37fF3oZd'
    b'vjAcBjPG/FSC9BX+oLE+3aIlLp7zuRZ9zP9l7tYelitZLDcvykCkCwWSOwa/Xl7pC3oIK7AgUIfo17L/9ubn4HJCOrb7cXo4p33D'
    b'S9t180pzVrjPaYYnhFVLOGhZDUeExFBeQ0/TIWrCKAbqPmcK1Xb+IHCvJMJ7Qg9EDRpeBhU0IuI9432tPILTxTOArXCDIQJ5ByKn'
    b'RKuhFKMuD34+G0pqDo5GHOw+QZ66DTrdbeVR7jBYOrhdPJTBqJbGFT/4HqHNhEsHkClKpS0UmFEUVbJ56MrtJJt0DCzlmCxLVvmd'
    b'LeaxrsWAL6jDl/H6DqwYpbUINxQBXtrc+sbls1dj8Uw2F1oyvRuA/Mq6Qn84u0ePdWPCi70OrSjf72GnpZvCKmGjYTuGgPFgMOWJ'
    b'Aj+u1+S5Rs3Beo8XcihgjsOgkoG3fpBLDcGiHIrbMB3zadClRVBYACNUmVIizwNlS4DyBDTyYMWV1w+MOL0geH0tz7qey4bHKSoh'
    b'FUQs7eCpkk3lHvS3OnLompMyEiB4OiPQjL6Ppzr+4x4icM56Lr3o/wnTez/7Ujp76izCMEkclDsQ2pswTQOu+QRoT2sHrdFlgG31'
    b'OY3b6XsvC4rINJgmOiIcTIG4FKtrKq8w59MuVUreDgh1fIcXZZGqEnZHV08rvFPGdWx2vwvh8BENvsl0QGGxTgCp4gHzY6BlXHMt'
    b'V+rG5QEtEmJyys60MAnKQjFgXbKOPUdy9WMN2+cN1j8pykR70meffzOZ985EG/nYXrd251LG9Jr7z+jdp+gvGXgCFIuJtQBKnnUh'
    b'fCBhqkPi6gAmlfNcN9gvRjkmKcW85jA+IBvHDMwGJmkIN5EuobA01GM1r6wfwcQ6ozwVdeu5lDsNGgdjePMwVViVC4RaR5gLBqoz'
    b'yn0dGjBKRLoUWS1nwNUJ3TMVxiCPSMiTy3CMDEiTNi1POdfSw3HIBv3Hk5PHZG6uNClhmtveXi2THu/MU/fAJ9s+YJrTBoFh8klP'
    b'M0W00KILeN3To+NlsldPRsCRnPPqTg5PV+tewOGXhPDsko6n+25lBKZ178bp6FM9bh2ifenWX8/7VJ43guLGZRJLwUIiiOQOXisF'
    b'RefAdR4kFD7hRR8oDCyG8g6REHW4/+JCq6Boz/qwA+JFAgGp2AdHHJmxdLir/nSspMVPslsUGCiBn/fIlpYDnC9ur/HHX5HJ96xB'
    b'2XaBDKirH+WhGUKsZTf0eWukvBArE+SF8ttg6uMxW5xURVIPxOocXaoX8GD9gL1EoCn1JCGOC7vOayfwGwWhsqRrIL2c9vjNT+8v'
    b'3nz/35akCgH0o7D8FNGrxC9ZA3PCReKgCllgvA1l15ksaIHFIbjnq0rStOOK4oUqMHZLaSAZv+HIuepCSvnLvagfITTBpDThyDsH'
    b'cCzFQWCHkO6A89UUhSc0Qz3KC/Ok8G3QJV/b46+xo3Dldllg//261ZkMwzpmAoYKXDUxyoSZvLBFkB2O2MXCjCi0cAHZYaJzLPnI'
    b'iPGeq1AapYD85qy4MeLKlI/fpJkhrwUKKhi4tT57LW+rGe2122toH7dfDugeciiMnZQ8emJ05HLDiO8JMqUbgv/m0x1B6dLHsKRM'
    b'WxbtHMxLGfEWME72IF98PfGc+fDtsXez1+LGk7Kf8Vrvtg6Llk+56wntekq1GF9PKOx4HtDxyJ3pMBVzW6o+KxW4g73zxN7J2x2l'
    b'd/m4ffIeZfWRpez01KR8oW2IRh3ZkoVwMozJuzC4Ls1VT+GVj40LlsO3RLWLTtiKZVuwp1hBGy5LOYhqMnHcAEEXj/Q+li5m9WnJ'
    b'/QE66wNKY24GwjS4NgigeGn7AQzKWBMJcriC5o7dwpnNn3EG9OhQ+hhKleKwJMegtl2fOG+kp+y5KjtO7RberAOOzMoSrSQVrVAb'
    b'YkleYyQw9ezY8PpS6mC3sIPbueFrKKLNfd+kdpO6c9PXbQ/pEueSTAX0um0BGmZ7wd24ejgwH946C9lkuc1V3tJAyFA11ZQZ9Uz3'
    b'q5oNdffpVF78UgvjL826RKM37Hd3zf93OlWLRhrne+O42l+wv62/cSkcZ6UVG8B7lmUWHspdzh003A/J3UL8Hp9FfUuZjAW8DxYj'
    b'OVAiFkanJDSwsVIiLlwjUDAobibtMpXC1Qhichnx+ito0gFDAFmYVfpVxb5CTHESbrMclIqoqQhF1zoDhYCoMEF39I6WQhkyGPr8'
    b'wd+0wiHP1kMhwa7h2EEJc0/3rAqxwpW/bNJUN7bQjTyVFdGXiyM0ol29lrqU3UlT7dOoNUFCUIX3Ux5o28WkJ/R68miEK5RncoOS'
    b'OiFaOKtTmWoragSJ4hPiLruWS6FyHeye3IVTvWGe9TdpJqLpWl6D0MV3wbT956z957n2Jz+Dnpwf6u2eqDghy0142nLwiKto3sJl'
    b'Y6PYq0mwQYtmuxppzlNeTektBSVN3kzAksU9AqrpYbduRYhENnGAyanqL20KQ5yfgilOR0z88f4afnkNv9AG0qaJf+gBjM07Tmet'
    b'9RWpcCQ9lpNVOxmO36qXQkim1ck79as/VzL//e3P3181Hdv0ZZ36Fize5WMCigfSfUNlwUb4nu2uissTBxPyCfABVu1cQA7YViBw'
    b'fT4yPa2LaJqUZvWFhTYYbcPrFyantKGfwvY39jCeH5f+NkNW4rGQSZSC9xDIOx7ipYAhZZN6u+K44lCCwAQdqb8k3X1CFKx/OnBm'
    b'vG7c52jITBApOrfrQylvsg84trUpoev8JYC5E7w9xU4wqDXRvql96WGfj8KuGVWV9xHigru1Ek1/U2ICc+IcYWxK5tHYmm3M5Llt'
    b'BCsQ+KGuVOUe7h9qSViwhPr3tANEFfaiX9Q9ZB90l/bNZMftvUntP3vT2lAPteJhoPcENKy49IfXqF9WSYzXmOQJ5M7/+4pi7Sph'
    b'z9cFa8N/cZUwfkXW/bPqgMlc4eimuVWEdSu5OMFLR/dk7y1UzcDgLiwwGOPb9kLU4+QaVQNAmKacXv3nChUIl0odoXaHtr1KuW1y'
    b'zahYCBgyeId4dTqi7DZQFVbHHM7oHOkk47lD6XMODk5cZrlycARZLeEIG23aL2fu+UrZEFjCEfdKLIDmpD2q8D4b0uOn0GzG/Tnd'
    b'CAClU3+jhZE6mM3FspJU4f2bNvbqmDAkfJ7bWbGxptqO3n5RwQMhMHLXw2EtQFoSKaJbVWYVcXXEpbS5RK5+K5AI2gxjGO4Ghldh'
    b'QTbkuFrIK4zpbrRwc3LVHWOYNaiYDSBtKajYk6gGI51Y6CTRV0SncH0ZRTmd59duyLrRYMmXEpo+DHB39ViW1Al2oNqGRbR7UCcR'
    b'l6LAixy8sF5LAAlwujNEzpbOs6/VnKLubpLp/juaKnI03pqa+L5cAa0gCj887VbidOklKcVKtNupNdCLPeGRbTfj7EDDrSF1Y9wO'
    b'mWuqJ+x7ewVkr0GK0EY03Ue675SgIWtrrxk+0VeH3/PV14HX3FHzJRDG1PBZB3POmXhBA9B2Dg+jtuO102EeLfl1bEO3ao8FrfSQ'
    b'kdm6NTnz9tpS3vCOlq3b2m1Ntb9rK2lq6r1AVZx6Z8j/p57bTf6hLKOVkbfxsrywpt7NsqN3UqLK8KCUwDQ8KozY3noYYujGsrKE'
    b'FGF0VuwFt7gX0fLTL26bxN3jKb9p+cgj7v8mPnJD16XFUrWzg29kdrCj8/ifucu6KY5eidirqMm1wbdcCSHmijEoVU2TazetdISL'
    b'poqIyq0C5VbmV+FOgwKPZZRVFEy4kjDRTuyKDjDBslVbsDCRo6rwF2btYbo9lgMDPTWkPIpMD4OFB5keqXPC0tccVSOscu5GrnRf'
    b'orIsf3d55XifV1JHT2MpPK2u7srU/entfcrJiVP6j3ZT89ae95fBtffJAaghDbbhwZ5P2fgrl2pLYWN7TkUnn7DE3o1PEqcxlNRv'
    b'xuB0lV33wLlcD+X18clykeXLfNOkcbGf2C7/uqiZRi9trbR1B71BT2Pwrujgw7kMKzSJAFVbPATLOsl46VfbtHIUTQS3/BMEbQid'
    b'17ZLdcLJvKlL3EVs6PE/TeAAB0wsrH+lw5RPbRfUTsfkH6V3r5dka4D/m2/WQ/0j6ePuFRHIhexA4TYjso+P/EEIEl2xEv6Mc+3n'
    b'4YqqFT4TNVxvz3lRdfF8TtqO/0j7+uRaAv5jMxY83GNIZcv4U/HHk+moleUx2l8eCNSutImsryyjgNC8uvdphz7iDkuPaifJWbDS'
    b'SEj3no8NiBYimPif944wUXs0E5Hq1qsZICEp3Q7VTiFg2fgvBv0dcTh+nNNR1TBsolBgZnNeZ5bffuiJvrb3Bsu0LW1ZEakHZiDG'
    b'I1ejf2zwUX96zH1PuKwbhWvFwJp63lhD2LqgHxSQKRUt9+RQ/wGiVIQ5LMT/LzXUjgsy9RtK7Spjx9GcNy4LeT5uQ7O4ABwMf/CU'
    b'QPNPPB7qNBgqWCNzJUqdkGHkykItMPwpoOQ8M/Pe1LnrHYX6pHwkRzfylPLAIrqBg6WEMszu4ZfKqTKaqRO1r7rJ3ACe9a0ut2WN'
    b'xtVM70xK7z5gGF0m5gZ9cVg44Km4E0Q6IipqUqs7IGHB6cGXrQTNJkMIcdbFOTfmg0aqK4bpK8bY8FCZGu9PPNAuonq/RnuuCO+C'
    b'phvSwXDydouYOP348BsP5PtiE13QkWiGvv21L7PfMvwkR0pfrJAxKFiPJjUDsLAUThacAdukBXIwj9SdbblFAd0VwLI0ki7tri3G'
    b'iXREbn1VDtS83MEBRVjUJvoIR38zs35PugE3AI1Y2hS2Y1p+PZ9lEQNRPEK7VoPmdwAk1LHL+9ejc26PjYQZpkHHUzC00ga3OwaY'
    b'ZzQGioP2QzQYbNMdHVjCgKMM2d4ta3jxACYbW5VFfOSh0SXxwNWTvpdAnR6RtakY6Qp7f30nTvKjF0d1phjbNilBi3d2l6Qrcwb4'
    b'sh2DDUOlq8H8AKX5JUcbXUz8VG7oWCJjsYRGqdWvPqS19iEexQ9UAoBYYxI39nOMXRRcHkjTo0QQNT2eniWq/PcnU2eYDpWIj1io'
    b'RK+7hDIjNN7tCts0tJBn9d4G0mp4Sy0NZ0R16EAw5FixmXJR93jhAH7F60A71g9IpJ80tjTJl7u8TmMrx8J3d6g4pygNqlzxMK/3'
    b'i1EfJ12dnjyM45TMs+wUmxWLh1l2RAlwVtSOlHgTVnL6F1GcKsKGruEsHwtxK3dow7WobrKelp83VA+tUTxN5fZ/IXHvZZo6aulH'
    b'0DqqcX+ibv1yvfoPG3ri0zQV1dXZh/dY9AvvSWgpX/d4CdhRwTZe7rm/6XJ5LxJ8b5xV14+26qZTrLRKeC8OER+KHDMLgSff+/dC'
    b'VVpoD+nW8la/GHEsU7nf5aeDU1PyU6rj7N5ozsQu1liZqRL9RxocLcGZxTyMPEaA/DEFZGplSQD63Jl4eo/5/fhvvF2N7zGB/ORY'
    b'RVlswoQj8uRI9S5Vyrg17IgGONVgOCdG1veL01jkjE4F7Hv00gN6dbSioqgn8CSKcbAoWuk8Lup0jKgjNmvVy5ezAqV4Yagn+Ej6'
    b'kbVmL8yR+aiPN8irJBoD6FTUVyj0NB7JWSbrlePea1EKt/8ivytvO7eosDV7CbLthXeMQJSOWL+f/UQC0nzyTneLx74xz1MT/ukx'
    b'17wapSe0ffrnFFQ9MVaoS21jv7cJX3xxSPBEbCk35uSlNZV7yiFZp7TXncNsVDrB93crHQv8bGeMKGKM3NcxG8vq6shbPrlQSe9Y'
    b'Nx/8ntFPXg5WZKuK3qW/ZANIT1SNr2igxUy16CDZRK0xNrMPDyNYUleNyr9i81qUyhn1zIg36BSMF98mvWh8tvLmEf9kGizPoxqF'
    b'vLYyicF/7BlF+WpF6sujeUlzjpwfNHLM+HNc89blXPGKLfAK7bJl8yLGN23xNvTZM9UHyFBxebPvsVtB81FLUQChoYmLZhYGxG66'
    b'x7zFAo3mu6Lc9jT+c85iZ7RupUk5IL1BVzRuGX3S4t27b6s6UBgpmX2iffDCefpIsSkSY+oDqpUjyultloduZjgvL2D45JiAeTC3'
    b'+RoZZ6W2LME1KJiflZltvHvknBjBkBSOqwSOdgx8U34Y22QICr3npwoaM6lNliB7dgZ/nqwzcejUjQEUDCE11IP2tXdZBqXoyQtB'
    b'o2g9DtkQvibgHppULIEvesG/ygtUSZ8Q1HWib7wvnc73NWJ2m4gvSOBd/5A+SBIl/GYoXpzHUks8NZE+Z05eXe2G7itrG5Zn2p2k'
    b'M/EZF3aPRmaCmaKe9XfG6Bb3vrkjSuUz67X0C5fmt09EOqT58RPtWyEnimvIdEFZSZp/W0cEFvjnPeazr1b/eU8/nLE5iKjHa3za'
    b'ow1g9tXc+GQiwut56sgi7XidFvqCBMrwrpie02ta4eQIUhHf/M61eQsCIb/eodI3RUD5pRo4r9EBCvhfPfE9EHkvhpQDDUX9bp2J'
    b'faexdoeMv+sx0E9aQq5j0isWKb5M/Ci+smLPRTarjVaePcd/YUloFHsuRrOb6I+tRYKMs3dyMnrkVd56oqr2fGn4MAmG2HjVASVX'
    b'O3zkq980iaLau+otypeecVdPfbeiyHX5+NSRq0h+lBVM35eZt6nctXu/SMObmq9MyCK7l3OOBs+hsU5NeKfmQTIHUO8b4F0snwMi'
    b'KvEI5VEphbDr6tfOuKTWmD2EEtSjW7p2J9EZJ2886ozS9h1xV5E9b//dR4+9H3Cl78qPumeIRFfn6ZCK+IwvcPiV2xd37l7C9Ydv'
    b'5jYM4ThSorYLn9noU5jGscnHCd4FbMRsezTNYkI3MZXk7SLQN6ob3ielPz0+OO1uh3fC1pqPiAF0ng6xAC4YoTHevPRHNpVxkcV6'
    b'sjwbN7eD+fTlV2ZEuAIOwZj7zmsq9dBzq1h0WMgSTgIOlS9qgIuqPBasG7rqw+22wPAyU5eSmyZ3YPNoX6QS8NVXr/kUgGrwszdh'
    b'nVblWVRX5cJKkKr4R9FoZknBK7XK7L+kaiXdATnqX4Hjn7oitGNoUiRrSik6E2ulBRDsgapvg9tiN7MFpMackgg3XuIoYxwdXsam'
    b'NZHL8/ZcdmFJn4IjT3BnCvQVDmRcQtuyvv/xQ7Oue1YhdPw2HYZO9rhX8p1co2emvrBkEFi/TE6J8ryQV2w7zlNv5ZmO4iIpFpr/'
    b'D3Nb/bY='
)
_OLD_PHYSICAL=zlib.decompress(base64.b64decode(_OLD_PHYSICAL_ZLIB_B64))
_OLD_SHA='fe13fe96a58c0f8816a38419074e4a5c12b65e0207b0d37e85a2d8abf82a76f5'
_IMPORT='from tools.annual_program.compiled_capture import capture_enabled as _annual_capture_enabled, flow_input_fields as _annual_flow_input_fields'
_EXPRESSION="_annual_flow_input_fields(dt_h=dt_h,nvars=nvars,raw_x=x,closure=closure,source_file=__file__,index_map={'gp':gp},coefficients={'steam':plant.steam_kWh_per_lb}) if _annual_capture_enabled() else {}"


def api():
    path=Path(__file__).parents[1]/'tools/annual_program/flow_source_contracts.py'
    assert path.is_file(), 'pure flow-source registry helper not implemented'
    return importlib.import_module('tools.annual_program.flow_source_contracts')


def candidate():
    assert hashlib.sha256(_OLD_PHYSICAL).hexdigest()==_OLD_SHA
    tree=ast.parse(_OLD_PHYSICAL)
    tree.body.insert(2,ast.parse(_IMPORT).body[0])
    leaf=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='solve_physical')
    leaf.body[-1].value.keys.append(None)
    leaf.body[-1].value.values.append(ast.parse(_EXPRESSION,mode='eval').body)
    return tree


def rendered(tree):return ast.unparse(ast.fix_missing_locations(tree)).encode()


def register_fixture_profile(module,monkeypatch,*,source=None,expression_sha=None,complete=True):
    for name in ('CAPTURE_NATIVE_SOURCE_CONTRACT','CAPTURE_RECORDER_SHA256',
                 'CAPTURE_AUXILIARY_SOURCE_CONTRACT','CAPTURE_LOGGING_EXPRESSION_AST_SHA256'):
        monkeypatch.setattr(module,name,None)
    # Simulate root editing immutable release literals, never a caller API.
    sources=dict(module.LEGACY_NATIVE_SOURCE_CONTRACT)
    sources['tools/operating_response/physical_balance.py']=hashlib.sha256(source).hexdigest() if source else 'a'*64
    monkeypatch.setattr(module,'CAPTURE_NATIVE_SOURCE_CONTRACT',sources)
    monkeypatch.setattr(module,'CAPTURE_RECORDER_SHA256','b'*64)
    if complete:
        monkeypatch.setattr(module,'CAPTURE_AUXILIARY_SOURCE_CONTRACT',{
            'tools/annual_program/compiled_capture.py':'c'*64,
            'tools/annual_program/compiled_flow_input.py':'d'*64})
        monkeypatch.setattr(module,'CAPTURE_LOGGING_EXPRESSION_AST_SHA256',expression_sha or 'e'*64)
    return sources


def test_legacy_literal_and_old_recorder_match_unmodified_reference():
    module=api()
    tree=ast.parse((Path(__file__).parents[1]/'tools/annual_program/witness.py').read_bytes())
    literal=next(ast.literal_eval(node.value) for node in tree.body if isinstance(node,ast.Assign)
        and any(isinstance(target,ast.Name) and target.id=='NATIVE_SOURCE_CONTRACT' for target in node.targets))
    assert module.LEGACY_NATIVE_SOURCE_CONTRACT==literal and len(literal)==14
    assert module.LEGACY_RECORDER_SHA256=='7640aba759d67d0a07a149e86c46f85eb11f647d099dbdda95506e314b21be62'
    assert module.match_native_source_profile(dict(literal))==module.LEGACY_PROFILE
    result=module.classify_recorder_source_pair(module.LEGACY_RECORDER_SHA256,dict(literal))
    assert result['supported'] is result['basic_witness_allowed'] is True
    assert result['compiled_source_profile_allowed'] is False
    assert result['source_authentication_performed'] is result['actual_execution_bound'] is False


def test_reviewed_release_pins_exact_files_and_cannot_be_enabled_by_caller():
    module=api()
    # The current checkout now contains a separate scientific model. Keep every
    # old release assertion against exact, parse-only d8d056d bytes instead.
    observed={name:hashlib.sha256(reviewed_source_bytes(name)).hexdigest() for name in module.LEGACY_NATIVE_SOURCE_CONTRACT}
    recognized=module.recognized_capture_native_source_contract(observed)
    assert recognized==observed
    for name,pin in recognized.items():
        assert hashlib.sha256(reviewed_source_bytes(name)).hexdigest()==pin
    additional=module.capture_source_extra_contract(observed)
    assert additional is not None
    for name,pin in dict(module.CAPTURE_AUXILIARY_SOURCE_CONTRACT,**additional).items():
        assert hashlib.sha256(reviewed_source_bytes(name)).hexdigest()==pin
    assert hashlib.sha256(reviewed_source_bytes('tools/annual_program/witness.py')).hexdigest()==module.CAPTURE_RECORDER_SHA256
    assert module.inspect_leaf_migration(reviewed_source_bytes('tools/operating_response/physical_balance.py'))['runtime_profile_approved'] is True
    result=module.classify_recorder_source_pair('b'*64,dict(module.LEGACY_NATIVE_SOURCE_CONTRACT))
    assert result['supported'] is result['compiled_source_profile_allowed'] is False
    with pytest.raises(TypeError):
        module.classify_recorder_source_pair('b'*64,dict(module.LEGACY_NATIVE_SOURCE_CONTRACT),approved_new_sha='b'*64)


def test_missing_release_approvals_still_disable_capture(monkeypatch):
    module=api();sources=deepcopy(module.CAPTURE_NATIVE_SOURCE_CONTRACT)
    recorder=module.CAPTURE_RECORDER_SHA256
    monkeypatch.setattr(module,'CAPTURE_AUXILIARY_SOURCE_CONTRACT',None)
    assert module.classify_recorder_source_pair(recorder,sources)['compiled_source_profile_allowed'] is False


def test_frozen12_capture_recorder_remains_explicitly_approved_when_current_pin_advances(monkeypatch):
    module=api()
    frozen='24ca59f219f3217a4755052f76b8deb96cbd0022b208a97e8aaad78091cd83ee'
    assert module.FROZEN12_CAPTURE_RECORDER_SHA256==frozen
    monkeypatch.setattr(module,'CAPTURE_RECORDER_SHA256','b'*64)
    result=module.classify_recorder_source_pair(frozen,module.CAPTURE_NATIVE_SOURCE_CONTRACT)
    assert result['supported'] is result['compiled_source_profile_allowed'] is True
    assert result['source_authentication_performed'] is False
    basic=module.classify_recorder_source_pair(frozen,module.LEGACY_NATIVE_SOURCE_CONTRACT)
    assert basic['supported'] is True and basic['compiled_source_profile_allowed'] is False
    assert module.classify_recorder_source_pair(frozen[:-1]+'f',module.CAPTURE_NATIVE_SOURCE_CONTRACT)['supported'] is False


def test_frozen12_capture_recorder_does_not_require_a_new_current_recorder(monkeypatch):
    module=api();monkeypatch.setattr(module,'CAPTURE_RECORDER_SHA256',None)
    result=module.classify_recorder_source_pair(
        '24ca59f219f3217a4755052f76b8deb96cbd0022b208a97e8aaad78091cd83ee',module.CAPTURE_NATIVE_SOURCE_CONTRACT)
    assert result['supported'] is result['compiled_source_profile_allowed'] is True


def test_frozen12_capture_recorder_cannot_follow_a_different_future_leaf(monkeypatch):
    module=api();source=deepcopy(module.CAPTURE_NATIVE_SOURCE_CONTRACT)
    source['tools/operating_response/physical_balance.py']='a'*64
    monkeypatch.setattr(module,'CAPTURE_NATIVE_SOURCE_CONTRACT',source)
    result=module.classify_recorder_source_pair(
        '24ca59f219f3217a4755052f76b8deb96cbd0022b208a97e8aaad78091cd83ee',source)
    assert result['supported'] is result['compiled_source_profile_allowed'] is False


@pytest.mark.parametrize('change',['native_sha','missing','extra','uppercase','bad_type','nonmapping','nonstring_key'])
def test_unknown_or_malformed_native_sources_never_match(change):
    module=api();sources=dict(module.LEGACY_NATIVE_SOURCE_CONTRACT)
    if change=='native_sha':sources['tools/operating_response/physical_balance.py']='0'*64
    elif change=='missing':sources.pop(next(iter(sources)))
    elif change=='extra':sources['tools/annual_program/unregistered.py']='0'*64
    elif change=='uppercase':sources[next(iter(sources))]=sources[next(iter(sources))].upper()
    elif change=='bad_type':sources[next(iter(sources))]=None
    elif change=='nonmapping':sources=list(sources.items())
    elif change=='nonstring_key':sources[7]='0'*64
    assert module.match_native_source_profile(sources) is None
    assert module.classify_recorder_source_pair(module.LEGACY_RECORDER_SHA256,sources)['supported'] is False


@pytest.mark.parametrize('value',[None,True,0,'0'*64,'F'*64,'7640'])
def test_unknown_recorder_is_separate_from_known_source_profile(value):
    module=api();sources=dict(module.LEGACY_NATIVE_SOURCE_CONTRACT)
    assert module.match_native_source_profile(sources)==module.LEGACY_PROFILE
    assert module.classify_recorder_source_pair(value,sources)['supported'] is False


def test_registered_new_recorder_old14_is_basic_without_compiled_qualification(monkeypatch):
    module=api();monkeypatch.setattr(module,'CAPTURE_RECORDER_SHA256','b'*64)
    result=module.classify_recorder_source_pair('b'*64,dict(module.LEGACY_NATIVE_SOURCE_CONTRACT))
    assert result['supported'] is result['basic_witness_allowed'] is True
    assert result['source_profile']==module.LEGACY_PROFILE
    assert result['compiled_source_profile_allowed'] is False


def test_source_only_profile_and_recorder_pair_do_not_imply_one_another(monkeypatch):
    module=api();sources=register_fixture_profile(module,monkeypatch)
    assert module.match_native_source_profile(sources)==module.CAPTURE_PROFILE
    assert module.classify_recorder_source_pair(module.LEGACY_RECORDER_SHA256,sources)['supported'] is False
    assert module.classify_recorder_source_pair('0'*64,sources)['supported'] is False
    result=module.classify_recorder_source_pair('b'*64,sources)
    assert result['supported'] is result['compiled_source_profile_allowed'] is True
    assert result['source_authentication_performed'] is result['actual_execution_bound'] is False
    assert result['requires_external_source_authentication'] is result['requires_exact_final_result_binding'] is True


def test_partial_future_literals_do_not_grant_runtime_capture(monkeypatch):
    module=api();sources=register_fixture_profile(module,monkeypatch,complete=False)
    assert module.match_native_source_profile(sources)==module.CAPTURE_PROFILE
    assert module.classify_recorder_source_pair('b'*64,sources)['supported'] is False


@pytest.mark.parametrize('change',['common_native','same_physical','extra_auxiliary','missing_auxiliary','unknown_auxiliary_sha'])
def test_incomplete_or_inconsistent_release_literal_configuration_fails_closed(monkeypatch,change):
    module=api();sources=register_fixture_profile(module,monkeypatch)
    if change=='common_native':sources['src/hospital_ies/solver_backend.py']='0'*64
    elif change=='same_physical':sources['tools/operating_response/physical_balance.py']=module.LEGACY_NATIVE_SOURCE_CONTRACT['tools/operating_response/physical_balance.py']
    else:
        auxiliary=dict(module.CAPTURE_AUXILIARY_SOURCE_CONTRACT)
        if change=='extra_auxiliary':auxiliary['tools/annual_program/anything.py']='f'*64
        elif change=='missing_auxiliary':auxiliary.pop(next(iter(auxiliary)))
        elif change=='unknown_auxiliary_sha':auxiliary[next(iter(auxiliary))]=None
        monkeypatch.setattr(module,'CAPTURE_AUXILIARY_SOURCE_CONTRACT',auxiliary)
    result=module.classify_recorder_source_pair('b'*64,sources)
    if change=='same_physical':
        assert result['compiled_source_profile_allowed'] is False
    else:assert result['supported'] is False


def test_external_authentication_requirements_are_explicit_and_registry_is_pure(monkeypatch):
    module=api();source=rendered(candidate())
    def forbidden(*args,**kwargs):raise AssertionError('pure registry attempted filesystem IO')
    monkeypatch.setattr('builtins.open',forbidden)
    result=module.inspect_leaf_migration(source)
    assert result['design_equivalent'] is True
    requirements=module.outer_verification_requirements()
    assert all(any(word in item for item in requirements) for word in ('source_root','caller','BINDINGS','exact final'))
    assert result['source_authentication_performed'] is False


def test_exact_logging_shape_is_design_only_until_all_literal_hashes_registered():
    module=api();result=module.inspect_leaf_migration(rendered(candidate()))
    assert result['shape_valid'] is result['original_function_ast_equal'] is result['original_module_ast_equal'] is True
    assert result['design_equivalent'] is True and result['runtime_profile_approved'] is False
    assert result['logging_expression_registered'] is False
    assert result['original_function_ast_sha256']=='6e9a0eab45589c888362aa344df5b68791caf9a58e9f2223e18804198ee1d164'
    assert result['original_module_ast_sha256']=='fe69b4bd0c5ba5b3946b10f87cb33710f393d00eab267c6a698b517296ffb581'


def test_actual_leaf_style_dict_construction_and_bounded_guard_metadata_are_readonly():
    module=api();tree=candidate()
    leaf=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='solve_physical')
    call=leaf.body[-1].value.values[-1].body
    next(item for item in call.keywords if item.arg=='index_map').value=ast.parse('dict(gp=gp,go=go)',mode='eval').body
    next(item for item in call.keywords if item.arg=='coefficients').value=ast.parse(
        "dict(HRSG_availability={key:availability[key] for key in ('HRSG_1','HRSG_2','HRSG_3')})",mode='eval').body
    call.keywords.append(ast.keyword(arg='domain_guard_inputs',value=ast.parse(
        "{key:(config or {})[key] for key in ('_m23_boiler_domain','_m24_exact_service_guard') if key in (config or {})}",mode='eval').body))
    report=module.inspect_leaf_migration(rendered(tree))
    assert report['design_equivalent'] is True and report['runtime_profile_approved'] is False


def test_exact_future_approved_profile_still_does_not_authenticate_caller_or_DTO(monkeypatch):
    module=api();source=rendered(candidate())
    draft=module.inspect_leaf_migration(source)
    sources=register_fixture_profile(module,monkeypatch,source=source,expression_sha=draft['logging_expression_ast_sha256'])
    result=module.inspect_leaf_migration(source)
    assert result['runtime_profile_approved'] is True
    assert result['source_authentication_performed'] is result['actual_execution_bound'] is False
    assert module.classify_recorder_source_pair('b'*64,sources)['compiled_source_profile_allowed'] is True
    # Comments preserve the erased AST but have an unregistered source SHA.
    changed=module.inspect_leaf_migration(source+b'\n# unregistered revision\n')
    assert changed['design_equivalent'] is True and changed['runtime_profile_approved'] is False


@pytest.mark.parametrize('change',['missing_import','duplicate_import','wrong_module','wrong_alias','wrong_symbol','nested_import',
    'missing_logging','duplicate_logging','nonfinal_logging','wrong_guard','guard_argument','wrong_helper','else_nonempty',
    'nested_state_query','snapshot_query','mutation_call','assignment_expression','comprehension','attribute_state','keyword_unpack',
    'physical_write','signature','extra_return_port','global_import','early_delegation_change','renamed_leaf','unknown_expression',
    'dict_positional','dict_unpack','unbounded_dictcomp','dictcomp_query'])
def test_only_one_pinned_import_and_final_readonly_logging_expression_can_erase(change):
    module=api();tree=candidate();leaf=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='solve_physical')
    imp=tree.body[2];ret=leaf.body[-1].value;expr=ret.values[-1]
    if change=='missing_import':tree.body.remove(imp)
    elif change=='duplicate_import':tree.body.insert(2,deepcopy(imp))
    elif change=='wrong_module':imp.module='tools.annual_program.foreign_capture'
    elif change=='wrong_alias':imp.names[0].asname='_different'
    elif change=='wrong_symbol':imp.names[0].name='unreviewed'
    elif change=='nested_import':tree.body.remove(imp);leaf.body.insert(1,imp)
    elif change=='missing_logging':ret.keys.pop();ret.values.pop()
    elif change=='duplicate_logging':ret.keys.append(None);ret.values.append(deepcopy(expr))
    elif change=='nonfinal_logging':ret.keys[-2:]=ret.keys[-2:][::-1];ret.values[-2:]=ret.values[-2:][::-1]
    elif change=='wrong_guard':expr.test.func.id='_different'
    elif change=='guard_argument':expr.test.args=[ast.Constant(value=True)]
    elif change=='wrong_helper':expr.body.func.id='_unreviewed_helper'
    elif change=='else_nonempty':expr.orelse=ast.parse("{'leaked':1}",mode='eval').body
    elif change=='nested_state_query':expr.body.keywords[0].value=ast.parse('state.query_current()',mode='eval').body
    elif change=='snapshot_query':expr.body.keywords[0].value=ast.parse('core.make_snapshot()',mode='eval').body
    elif change=='mutation_call':expr.body.keywords[0].value=ast.parse('x.append(0)',mode='eval').body
    elif change=='assignment_expression':expr.body.keywords[0].value=ast.parse('(x := None)',mode='eval').body
    elif change=='comprehension':expr.body.keywords[0].value=ast.parse('[v for v in x]',mode='eval').body
    elif change=='attribute_state':expr.body.keywords[0].value=ast.parse('state.energy',mode='eval').body
    elif change=='keyword_unpack':expr.body.keywords.append(ast.keyword(arg=None,value=ast.Name(id='config',ctx=ast.Load())))
    elif change=='physical_write':leaf.body.insert(-1,ast.parse('x[0]=999.0').body[0])
    elif change=='signature':leaf.args.defaults[-1]=ast.Constant(value='changed')
    elif change=='extra_return_port':ret.keys.insert(0,ast.Constant(value='extra_physical'));ret.values.insert(0,ast.Constant(value=0))
    elif change=='global_import':tree.body.insert(3,ast.parse('import injected_dependency').body[0])
    elif change=='early_delegation_change':
        delegation=next(node for node in ast.walk(leaf) if isinstance(node,ast.Return) and isinstance(node.value,ast.Call))
        delegation.value.func.id='different_execution'
    elif change=='renamed_leaf':leaf.name='different_solve'
    elif change=='unknown_expression':expr.body.keywords[0].value=ast.parse('a+b',mode='eval').body
    elif change=='dict_positional':expr.body.keywords[0].value=ast.parse('dict(config)',mode='eval').body
    elif change=='dict_unpack':expr.body.keywords[0].value=ast.parse('dict(**config)',mode='eval').body
    elif change=='unbounded_dictcomp':expr.body.keywords[0].value=ast.parse('{key:config[key] for key in config}',mode='eval').body
    elif change=='dictcomp_query':expr.body.keywords[0].value=ast.parse("{key:state.query_current() for key in ('x',)}",mode='eval').body
    result=module.inspect_leaf_migration(rendered(tree))
    assert result['design_equivalent'] is False and result['runtime_profile_approved'] is False
    assert result['reasons']


@pytest.mark.parametrize('source',[None,7,b'\xff',b'def broken(','\ud800'])
def test_malformed_source_is_rejected_without_execution(source):
    result=api().inspect_leaf_migration(source)
    assert result['design_equivalent'] is result['runtime_profile_approved'] is False


def test_registry_expected_outputs_cannot_mutate_literal_or_future_decisions():
    module=api();original=dict(module.LEGACY_NATIVE_SOURCE_CONTRACT)
    requirements=module.outer_verification_requirements()
    assert type(requirements) is tuple
    result=module.classify_recorder_source_pair(module.LEGACY_RECORDER_SHA256,original)
    result['source_profile']='forged'
    assert module.match_native_source_profile(original)==module.LEGACY_PROFILE
