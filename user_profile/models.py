from django.db import models
from django.utils.translation import gettext_lazy as _

class Profile(models.Model):
    name = models.CharField(_("Nome"), max_length=100)
    photo = models.URLField(_("Foto"))
    job = models.CharField(_("Cargo"), max_length=100)
    location = models.CharField(_("Localização"), max_length=100)
    phone = models.CharField(_("Telefone"), max_length=20)
    email = models.EmailField(_("Email"))
    
    class Meta:
        verbose_name = _("Perfil")
        verbose_name_plural = _("Perfis")
    
    def __str__(self):
        return self.name

class HardSkill(models.Model):
    profile = models.ForeignKey(Profile, related_name="hard_skills", on_delete=models.CASCADE)
    name = models.CharField(_("Nome"), max_length=50)
    logo = models.URLField(_("Logo"))
    url = models.URLField(_("URL"), blank=True, null=True)
    
    class Meta:
        verbose_name = _("Hard Skill")
        verbose_name_plural = _("Hard Skills")
    
    def __str__(self):
        return self.name

class SoftSkill(models.Model):
    profile = models.ForeignKey(Profile, related_name="soft_skills", on_delete=models.CASCADE)
    name = models.CharField(_("Nome"), max_length=50)
    
    class Meta:
        verbose_name = _("Soft Skill")
        verbose_name_plural = _("Soft Skills")
    
    def __str__(self):
        return self.name

class Language(models.Model):
    profile = models.ForeignKey(Profile, related_name="languages", on_delete=models.CASCADE)
    name = models.CharField(_("Idioma"), max_length=50)
    
    class Meta:
        verbose_name = _("Idioma")
        verbose_name_plural = _("Idiomas")
    
    def __str__(self):
        return self.name

class PortfolioItem(models.Model):
    profile = models.ForeignKey(Profile, related_name="portfolio", on_delete=models.CASCADE)
    name = models.CharField(_("Nome"), max_length=100)
    url = models.URLField(_("URL"))
    is_github = models.BooleanField(_("É GitHub?"), default=False)
    
    class Meta:
        verbose_name = _("Item do Portfólio")
        verbose_name_plural = _("Itens do Portfólio")
    
    def __str__(self):
        return self.name

class ProfessionalExperience(models.Model):
    profile = models.ForeignKey(Profile, related_name="experiences", on_delete=models.CASCADE)
    name = models.CharField(_("Cargo"), max_length=100)
    period = models.CharField(_("Período"), max_length=50)
    description = models.TextField(_("Descrição"))
    
    class Meta:
        verbose_name = _("Experiência Profissional")
        verbose_name_plural = _("Experiências Profissionais")
    
    def __str__(self):
        return f"{self.name} ({self.period})"