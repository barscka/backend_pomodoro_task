from django.contrib import admin
from .models import Profile, HardSkill, SoftSkill, Language, PortfolioItem, ProfessionalExperience

admin.site.register(Profile)
admin.site.register(HardSkill)
admin.site.register(SoftSkill)
admin.site.register(Language)
admin.site.register(PortfolioItem)
admin.site.register(ProfessionalExperience)